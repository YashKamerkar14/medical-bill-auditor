"""System D: the full system (Milestone 3): rules plus the model where language helps.

Runs every case through the full graph with a GuardedLLM (cache, per-run and
per-day caps). If a model call fails for quota or availability, the run stops
before scoring: cached answers make the next run resume cheaply.

Explanation metrics (docs/decisions/0008):
- status counts: explained / insufficient evidence / unverified (dropped) / not explained;
- raw quote accuracy: verified quotes / quotes the model proposed (before the guardrail);
- final evidence validity: reported explanations whose every quote verifies when
  re-checked here, independently of the graph (target 100%);
- section match: explained flags citing a section where that rule is set out.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from bill_auditor.agent.graph import STEP_BUDGET, build_graph, run_audit
from bill_auditor.agent.tracing import Tracer
from bill_auditor.checks.models import to_predicted
from bill_auditor.db import connect
from bill_auditor.evaluation.schemas import Case, PredictedFlag
from bill_auditor.guardrails.quotes import verify_quote

GENERAL_SECTIONS = {
    "unbundling": {"I.A", "I.E"},       # unbundling defined (I.A); PTP edits and modifiers (I.E)
    "quantity": {"I.V", "XI.V"},        # medically unlikely edits
    "duplicate": {"I.E", "I.V"},        # repeat-procedure modifiers 76/77/91 (I.E, I.V)
}


def acceptable_sections(flag) -> set[str]:
    sections = set(GENERAL_SECTIONS.get(flag.check, set()))
    table = next((e.values for e in flag.evidence if e.source == "table_row"), {})
    sections |= set(re.findall(r"manual ([IVX]+\.[A-Z])", str(table.get("rationale", ""))))
    return sections


@dataclass
class FullRun:
    outputs: dict[str, list[PredictedFlag]] = field(default_factory=dict)
    steps: dict[str, int] = field(default_factory=dict)
    explanations: list[dict] = field(default_factory=list)
    stopped: str | None = None
    seconds: float = 0.0

    @property
    def within_budget(self) -> bool:
        return all(s <= STEP_BUDGET for s in self.steps.values())


def run_full_system(cases: list[Case], db_path: Path, llm, chroma, embedder, trace_dir: Path | None = None,
                    lookups=None) -> FullRun:
    graph = build_graph(db_path, llm=llm, chroma=chroma, embedder=embedder, lookups=lookups)
    result = FullRun()
    start = time.perf_counter()
    with connect(db_path) as conn:
        for case in cases:
            tracer = Tracer(trace_dir / f"{case.case_id}.jsonl" if trace_dir else None)
            run = run_audit(graph, case.bill, case.eob, tracer=tracer)
            if run.state.get("model_failures"):
                result.stopped = f"{case.case_id}: {run.state['model_failures'][0]}"
                break
            report = run.report
            result.outputs[case.case_id] = to_predicted(report.errors + report.benchmarks, report.cannot_check)
            result.steps[case.case_id] = report.steps_used
            flags = {f.flag_id: f for f in report.errors}
            for flag_id, e in report.explanations.items():
                independent = all(verify_quote(conn, q.passage_id, q.text) is None for q in e.quotes)
                result.explanations.append({
                    "case_id": case.case_id, "flag_id": flag_id, "status": e.status, "rewrites": e.rewrites,
                    "writes": e.writes, "quotes_proposed": e.quotes_proposed, "quotes_verified": e.quotes_verified,
                    "final_quotes_valid": independent if e.status == "explained" else None,
                    "section_match": (bool(set(e.sections) & acceptable_sections(flags[flag_id]))
                                      if e.status == "explained" and flag_id in flags else None),
                })
    result.seconds = time.perf_counter() - start
    return result


def explanation_metrics(explanations: list[dict]) -> dict:
    from bill_auditor.evaluation.metrics import wilson
    from dataclasses import asdict

    status = {s: sum(e["status"] == s for e in explanations)
              for s in ("explained", "insufficient_evidence", "unverified", "not_explained")}
    explained = [e for e in explanations if e["status"] == "explained"]
    proposed = sum(e["quotes_proposed"] for e in explanations)
    verified = sum(e["quotes_verified"] for e in explanations)
    return {
        "flags_sent_to_explain": len(explanations),
        "status": status,
        "explained_rate": asdict(wilson(status["explained"], len(explanations))),
        "raw_quote_accuracy": asdict(wilson(verified, proposed)),
        "final_evidence_validity": asdict(wilson(sum(bool(e["final_quotes_valid"]) for e in explained), len(explained))),
        "section_match_rate": asdict(wilson(sum(bool(e["section_match"]) for e in explained), len(explained))),
        "regenerated": sum(e["writes"] > 1 for e in explanations),
        "rewritten_queries": sum(e["rewrites"] for e in explanations),
    }
