"""Milestone 4, Part B: run the ReAct agent on the locked sample and compare systems A-D.

Running: each case's result is saved under DATA_DIR/eval/runs/react/<model>/,
keyed by the agent's prompt version, so a stopped run resumes and replays from
the model cache. A model failure (quota, cap, outage) stops the run before
anything partial is saved.

Scoring (pre-registered in docs/decisions/0009): every system is scored on the
same sample cases; the three primary contrasts are computed on the held-out
part with Holm correction; everything else is descriptive.
"""

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from bill_auditor.baselines.model_alone import build_user_message
from bill_auditor.baselines.react_agent import REACT_SYSTEM, ReActStep, Toolbox, build_react_graph, run_agent
from bill_auditor.db import connect
from bill_auditor.evaluation.metrics import is_clean_case, score_case, summarise
from bill_auditor.evaluation.schemas import Case, PredictedFlag
from bill_auditor.evaluation.stats import holm, mcnemar_exact, paired_bootstrap_diff
from bill_auditor.guardrails.pii import UnsafePayload
from bill_auditor.guardrails.quotes import verify_quote
from bill_auditor.llm.gemini import ModelCallFailed, RateLimited
from bill_auditor.llm.guarded import BudgetExhausted

PROMPT_VERSION = hashlib.sha256((REACT_SYSTEM + json.dumps(ReActStep.model_json_schema())).encode()).hexdigest()[:12]
MODEL_FAILURES = (RateLimited, ModelCallFailed, BudgetExhausted, UnsafePayload)


def required_lookups(case: Case) -> set[tuple]:
    """Lookups the answer key depends on: every Z-code's unit limit, every Z-code pair on the same date."""
    z = [l for l in case.bill.lines if l.code.startswith("Z")]
    needed = {("unit", l.code) for l in z}
    for i, a in enumerate(z):
        for b in z[i + 1:]:
            if a.date_of_service == b.date_of_service and a.code != b.code:
                needed.add(("pair", frozenset((a.code, b.code))))
    return needed


def lookups_made(tool_log: list) -> set[tuple]:
    made = set()
    for tool, args in tool_log:
        if tool == "get_unit_limit" and "code" in args:
            made.add(("unit", str(args["code"]).upper()))
        if tool == "check_code_pair" and {"code_a", "code_b"} <= set(args):
            made.add(("pair", frozenset((str(args["code_a"]).upper(), str(args["code_b"]).upper()))))
    return made


def run_react(cases: list[Case], out_dir: Path, llm, db_path: Path, lookups, limit: int | None = None) -> dict:
    """`lookups` serve the agent's tools (direct or over MCP); `db_path` is read directly to verify its quotes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    graph = build_react_graph(llm, Toolbox(lookups))
    done = skipped = 0
    stopped = None
    for case in cases:
        path = out_dir / f"{case.case_id}.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8"))["prompt_version"] == PROMPT_VERSION:
            skipped += 1
            continue
        if limit is not None and done >= limit:
            break
        before = (llm.calls, llm.cache_hits, dict(llm.tokens))
        try:
            out = run_agent(graph, build_user_message(case))
        except MODEL_FAILURES as error:
            stopped = f"{case.case_id}: {error}"
            break
        answer = out["final"]
        with closing(connect(db_path)) as conn:
            quotes = [{"passage_id": q.passage_id, "text": q.text,
                       "valid": verify_quote(conn, q.passage_id, q.text) is None} for q in (answer.quotes if answer else [])]
        needed, made = required_lookups(case), lookups_made(out["tool_log"])
        record = {
            "case_id": case.case_id, "system": "react_agent", "model": llm.model, "prompt_version": PROMPT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "valid": answer is not None,
            "flags": [f.model_dump() for f in answer.flags] if answer else None,
            "quotes": quotes, "turns": out["turns"], "exhausted": out["exhausted"],
            "tool_calls": [[t, a] for t, a in out["tool_log"]],
            "required_lookups": len(needed), "required_lookups_made": len(needed & made),
            "model_calls_new": llm.calls - before[0], "model_calls_cached": llm.cache_hits - before[1],
            "tokens": {k: llm.tokens[k] - before[2].get(k, 0) for k in llm.tokens},
            "seconds": out["seconds"],
        }
        path.write_text(json.dumps(record, indent=1, default=list), encoding="utf-8")
        done += 1
    return {"done": done, "skipped": skipped, "stopped": stopped}


def load_react(out_dir: Path) -> dict[str, dict]:
    records = {}
    for path in sorted(out_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["prompt_version"] == PROMPT_VERSION:
            records[record["case_id"]] = record
    return records


def react_outputs(records: dict[str, dict]) -> dict[str, list[PredictedFlag] | None]:
    return {cid: [PredictedFlag(**f) for f in r["flags"]] if r["valid"] else None for cid, r in records.items()}


def fully_correct(case: Case, flags) -> bool:
    s = score_case(case, flags)
    return s.output_valid and not s.missed and not s.false_flags and s.trap_passed is not False


def _precision(rows, system: str):
    found = sum(r[system]["found"] for r in rows)
    raised = sum(r[system]["raised"] for r in rows)
    return found / raised if raised else None


def compare(cases: list[Case], outputs: dict[str, dict]) -> dict:
    """outputs: system name -> {case_id: flags or None}. Every system is scored on every case."""
    systems = list(outputs)
    summaries = {name: summarise(cases, outputs[name]) for name in systems}
    correct = {name: [fully_correct(c, outputs[name].get(c.case_id)) for c in cases] for name in systems}
    rows = []
    for c in cases:
        row = {}
        for name in systems:
            s = score_case(c, outputs[name].get(c.case_id))
            found = sum(1 for e, _ in s.matched if e.kind == "error")
            raised = found + sum(1 for p in s.false_flags if p.kind == "error")
            row[name] = {"found": found, "raised": raised}
        rows.append(row)
    tests = {
        "C_vs_A_fully_correct": mcnemar_exact(correct["C"], correct["A"]),
        "C_vs_D_fully_correct": mcnemar_exact(correct["C"], correct["D"]),
    }
    tests["C_vs_A_precision"] = paired_bootstrap_diff(rows, lambda r: _precision(r, "C"), lambda r: _precision(r, "A"))
    adjusted = holm({name: test["p_value"] for name, test in tests.items()})
    clean = [c for c in cases if is_clean_case(c)]
    return {
        "cases": len(cases),
        "fully_correct_rate": {name: round(sum(v) / len(v), 3) for name, v in correct.items()},
        "false_alarms_clean": {name: summaries[name]["false_alarm_rate_clean"] for name in systems},
        "clean_cases": len(clean),
        "summaries": summaries,
        "primary_tests": tests,
        "holm_adjusted": adjusted,
    }


def agent_operations(records: dict[str, dict], case_ids: list[str]) -> dict:
    rows = [records[c] for c in case_ids if c in records]
    if not rows:
        return {}
    quotes = [q for r in rows for q in r["quotes"]]
    needed = sum(r["required_lookups"] for r in rows)
    injection = [r for r in rows if r["case_id"].startswith("hard-i")]
    return {
        "cases": len(rows),
        "invalid_or_exhausted": sum(not r["valid"] for r in rows),
        "exhausted": sum(r["exhausted"] for r in rows),
        "mean_turns": round(sum(r["turns"] for r in rows) / len(rows), 2),
        "mean_tool_calls": round(sum(len(r["tool_calls"]) for r in rows) / len(rows), 2),
        "mean_model_calls": round(sum(r["model_calls_new"] + r["model_calls_cached"] for r in rows) / len(rows), 2),
        "mean_input_tokens": round(sum(r["tokens"].get("input_tokens", 0) for r in rows) / len(rows)),
        "required_lookup_recall": f"{sum(r['required_lookups_made'] for r in rows)}/{needed}",
        "quotes_valid": f"{sum(q['valid'] for q in quotes)}/{len(quotes)}",
        "injection_cases": len(injection),
    }
