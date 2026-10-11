"""Run and score the systems.

    uv run python -m bill_auditor.evaluation.run_eval run-baseline --split dev --max-requests 25
    uv run python -m bill_auditor.evaluation.run_eval score --split dev
    uv run python -m bill_auditor.evaluation.run_eval score-rules --split dev
    uv run python -m bill_auditor.evaluation.run_eval score-full --split dev --max-calls 300

Cases are processed in a fixed pseudo-random order (by a hash of the case id),
so a run stopped part-way by the free-tier quota has still answered a fair
sample of the set. Scoring covers the cases answered so far and says so.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from datetime import datetime, timezone

from bill_auditor.agent.graph import STEP_BUDGET
from bill_auditor.baselines.model_alone import PROMPT_VERSION, load_outputs, run
from bill_auditor.baselines.full_system import explanation_metrics, run_full_system
from bill_auditor.baselines.rules_only import run_rules_only
from bill_auditor.config import PROJECT_ROOT, load_paths
from bill_auditor.db import connect
from bill_auditor.evaluation.generator import read_cases
from bill_auditor.evaluation.metrics import format_report, summarise
from bill_auditor.evaluation.schemas import Case
from bill_auditor.llm.gemini import DEFAULT_MODEL, GeminiClient

HARD_CASES = PROJECT_ROOT / "eval" / "hard_cases" / "cases.jsonl"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"


def case_files(paths) -> list:
    return [paths.eval_dir / "generated" / "cases.jsonl", HARD_CASES]


def load_cases(paths, split: str) -> list[Case]:
    cases = [c for f in case_files(paths) for c in read_cases(f)]
    if split != "all":
        cases = [c for c in cases if c.split == split]
    return sorted(cases, key=lambda c: hashlib.sha256(c.case_id.encode()).hexdigest())


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def run_dir(paths, model: str):
    return paths.eval_dir / "runs" / "model_alone" / model


def cmd_run(args) -> None:
    paths = load_paths()
    cases = load_cases(paths, args.split)
    client = GeminiClient(model_name())
    report = run(cases, run_dir(paths, client.model), client, args.max_requests, args.min_interval)
    done = len(load_outputs(run_dir(paths, client.model)).keys() & {c.case_id for c in cases})
    print(f"sent {report.sent} requests ({report.busy_retries} busy-server retries), {report.invalid} invalid answers; "
          f"{done}/{len(cases)} {args.split} cases answered")
    if report.stopped_reason:
        print(f"stopped: {report.stopped_reason}")


def _file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_scores(paths, system: str, title: str, split: str, cases: list[Case], answered: list[Case],
                 outputs: dict, extra: dict) -> None:
    """Print summaries (all / generated / hard) and save them with full provenance to eval/results/."""
    groups = {
        "all": answered,
        "generated": [c for c in answered if c.source == "generated"],
        "hard": [c for c in answered if c.source == "hard"],
    }
    summaries = {name: summarise(group, outputs) for name, group in groups.items() if group}
    for name, summary in summaries.items():
        print(format_report(summary, f"{title}, {split} split, {name} cases"), end="\n\n")

    with connect(paths.sqlite_path) as conn:
        releases = [dict(r) for r in conn.execute("SELECT dataset, label, synthetic FROM releases ORDER BY dataset")]
    result = {
        "system": system,
        **extra,
        "split": split,
        "cases_answered": len(answered),
        "cases_in_split": len(cases),
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "table_releases": releases,
        "case_file_sha256": {f.name if f.parent.name != "hard_cases" else "hard_cases.jsonl": _file_hash(f)
                             for f in case_files(paths)},
        "summaries": summaries,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    milestone = {"rules_only": "m2", "full_system": "m3"}.get(system, "m1")
    out = RESULTS_DIR / f"{milestone}_{system}_{split}_{stamp}.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"saved {out.relative_to(PROJECT_ROOT)} ({len(answered)}/{len(cases)} cases answered)")


def cmd_score(args) -> None:
    paths = load_paths()
    model = model_name()
    cases = load_cases(paths, args.split)
    outputs = load_outputs(run_dir(paths, model))
    answered = [c for c in cases if c.case_id in outputs]
    if not answered:
        print("No answered cases yet: run `run-baseline` first.")
        return
    _save_scores(paths, "model_alone", f"Model alone ({model})", args.split, cases, answered, outputs,
                 {"model": model, "prompt_version": PROMPT_VERSION})


def cmd_score_rules(args) -> None:
    """Run the rules-only graph on every case (no model, no quota) and score it."""
    paths = load_paths()
    cases = load_cases(paths, args.split)
    trace_dir = paths.data_dir / "traces" / f"rules_only_{args.split}" if args.trace else None
    run_result = run_rules_only(cases, paths.sqlite_path, trace_dir)
    steps = list(run_result.steps.values())
    budget = {
        "step_budget": STEP_BUDGET,
        "max_steps_used": max(steps),
        "runs_within_budget": sum(s <= STEP_BUDGET for s in steps),
        "runs": len(steps),
        "all_within_budget": run_result.within_budget,
        "stopped_runs": sum(s == "stopped" for s in run_result.statuses.values()),
        "flags_dropped_by_verify": sum(run_result.dropped.values()),
    }
    print(f"step budget: {budget['runs_within_budget']}/{budget['runs']} runs within {STEP_BUDGET} steps "
          f"(max used {budget['max_steps_used']}); verify dropped {budget['flags_dropped_by_verify']} flag(s)",
          end="\n\n")
    _save_scores(paths, "rules_only", "Rules only (no model)", args.split, cases, cases, run_result.outputs,
                 {"model": "none", "step_budget_check": budget})


def cmd_score_full(args) -> None:
    """Run the full system (rules + model for matching and explanations) and score it."""
    from bill_auditor.ingest.index_manual import open_chroma
    from bill_auditor.llm.guarded import GuardedLLM
    from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

    paths = load_paths()
    cases = load_cases(paths, args.split)
    model = model_name()
    llm = GuardedLLM(GeminiClient(model), cache_dir=paths.eval_dir / "llm_cache" / model,
                     usage_dir=paths.data_dir / "llm_usage", max_calls=args.max_calls, daily_cap=args.daily_cap,
                     min_interval_s=args.min_interval)
    trace_dir = paths.data_dir / "traces" / f"full_system_{args.split}" if args.trace else None
    run_result = run_full_system(cases, paths.sqlite_path, llm, open_chroma(paths.chroma_dir),
                                 FastEmbedEmbedder(paths.models_dir), trace_dir)
    print(f"model calls: {llm.calls} new, {llm.cache_hits} from cache, {llm.busy_retries} busy retries; "
          f"{llm.used_today()} used today; {run_result.seconds:.0f}s")
    if run_result.stopped:
        print(f"STOPPED before scoring ({len(run_result.outputs)}/{len(cases)} cases done): {run_result.stopped}")
        print("Run the same command again later: cached answers are reused.")
        return
    explain = explanation_metrics(run_result.explanations)
    steps = list(run_result.steps.values())
    print(f"step budget: {sum(s <= STEP_BUDGET for s in steps)}/{len(steps)} runs within {STEP_BUDGET} (max {max(steps)})")
    print(f"explanations: {explain['status']} | raw quote accuracy {explain['raw_quote_accuracy']['rate']} | "
          f"final evidence validity {explain['final_evidence_validity']['rate']} | "
          f"section match {explain['section_match_rate']['rate']}", end="\n\n")
    _save_scores(paths, "full_system", f"Full system ({model})", args.split, cases, cases, run_result.outputs,
                 {"model": model, "explanations": explain,
                  "step_budget_check": {"step_budget": STEP_BUDGET, "max_steps_used": max(steps),
                                        "all_within_budget": run_result.within_budget},
                  "model_calls": {"new": llm.calls, "cached": llm.cache_hits, "busy_retries": llm.busy_retries,
                                  "tokens": llm.tokens},
                  "seconds": round(run_result.seconds, 1)})


def cmd_score_retrieval(args) -> None:
    """Milestone 4, Part A: every search mode on the labelled queries (no model calls)."""
    from bill_auditor.evaluation.retrieval_eval import evaluate
    from bill_auditor.ingest.index_manual import open_chroma
    from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

    paths = load_paths()
    queries = json.loads((PROJECT_ROOT / "eval" / "retrieval_queries" / "queries.json").read_text(encoding="utf-8"))["queries"]
    with connect(paths.sqlite_path) as conn:
        titles = {r["section_id"]: r["title"] for r in conn.execute("SELECT section_id, title FROM manual_sections WHERE text != ''")}
        result = evaluate(conn, open_chroma(paths.chroma_dir), FastEmbedEmbedder(paths.models_dir),
                          FastEmbedEmbedder(paths.models_dir, query_prefix=""), queries, titles)
    print(f"{'mode':22} {'hit@1':>6} {'hit@5':>6} {'MRR':>6} {'chunk hit@5':>11}")
    for mode, row in result["table"].items():
        print(f"{mode:22} {row['hit@1']:>6} {row['hit@5']:>6} {row['mrr']:>6} {row.get('chunk_hit@5', ''):>11}")
    print()
    print("primary tests:", json.dumps(result["primary_tests"], indent=1))
    print("relevance floor:", result["relevance_floor"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"m4_retrieval_{stamp}.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"saved {out.relative_to(PROJECT_ROOT)}")


SAMPLE_FILE = PROJECT_ROOT / "eval" / "samples" / "m4_agent_sample.json"


def _m4_llm(paths, args):
    """Gemini free tier first, the same model via OpenRouter when the free quota is gone ($1 cap overall)."""
    from bill_auditor.llm.fallback import FallbackClient
    from bill_auditor.llm.guarded import GuardedLLM
    from bill_auditor.llm.openrouter import OpenRouterClient

    model = model_name()
    client = FallbackClient(GeminiClient(model), OpenRouterClient(paths.data_dir / "llm_usage" / "openrouter_spend_m4.json",
                                                                  cap_usd=args.cap_usd))
    return GuardedLLM(client, cache_dir=paths.eval_dir / "llm_cache" / model, usage_dir=paths.data_dir / "llm_usage",
                      max_calls=args.max_calls, daily_cap=args.daily_cap, min_interval_s=args.min_interval), client


def _sample_cases(paths, part: str) -> list[Case]:
    ids = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))[part]
    by_id = {c.case_id: c for c in load_cases(paths, "all")}
    return [by_id[i] for i in ids]


def cmd_run_react(args) -> None:
    from bill_auditor.evaluation.m4_agent import run_react
    from bill_auditor.ingest.index_manual import open_chroma
    from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

    paths = load_paths()
    llm, client = _m4_llm(paths, args)
    cases = _sample_cases(paths, args.part)
    out_dir = paths.eval_dir / "runs" / "react" / llm.model
    report = run_react(cases, out_dir, llm, paths.sqlite_path, open_chroma(paths.chroma_dir),
                       FastEmbedEmbedder(paths.models_dir), limit=args.limit)
    spent = client.overflow.spent()
    print(f"{args.part}: {report['done']} run, {report['skipped']} already done; model calls {llm.calls} new / "
          f"{llm.cache_hits} cached; providers {client.calls}; OpenRouter spend so far ${spent:.4f} of ${args.cap_usd:.2f}")
    if report["stopped"]:
        print(f"STOPPED: {report['stopped']}")


def cmd_score_m4(args) -> None:
    """Score A (model alone), B (rules only), C (ReAct agent) and D (full system) on the same sample cases."""
    from bill_auditor.evaluation.m4_agent import PROMPT_VERSION as REACT_VERSION
    from bill_auditor.evaluation.m4_agent import agent_operations, compare, load_react, react_outputs
    from bill_auditor.ingest.index_manual import open_chroma
    from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

    paths = load_paths()
    llm, client = _m4_llm(paths, args)
    cases = _sample_cases(paths, args.part)
    ids = [c.case_id for c in cases]
    records = load_react(paths.eval_dir / "runs" / "react" / llm.model)
    missing = [i for i in ids if i not in records]
    if missing:
        print(f"ReAct answers missing for {len(missing)} case(s): run `run-react --part {args.part}` first.")
        return

    a_dir = run_dir(paths, llm.model)
    report = run([c for c in cases if c.case_id not in load_outputs(a_dir)], a_dir, client, args.max_calls, args.min_interval)
    a_outputs = load_outputs(a_dir)
    if any(i not in a_outputs for i in ids):
        print(f"Model-alone answers still missing ({report.stopped_reason}); run again later.")
        return
    full = run_full_system(cases, paths.sqlite_path, llm, open_chroma(paths.chroma_dir), FastEmbedEmbedder(paths.models_dir))
    if full.stopped:
        print(f"Full system STOPPED: {full.stopped}; run again later (cached answers are reused).")
        return
    outputs = {"A": {i: a_outputs[i] for i in ids}, "B": run_rules_only(cases, paths.sqlite_path).outputs,
               "C": react_outputs({i: records[i] for i in ids}), "D": full.outputs}
    result = compare(cases, outputs)
    result["agent_operations"] = agent_operations(records, ids)
    result["part"] = args.part
    result["models"] = {"A": llm.model, "B": "none", "C": llm.model, "D": llm.model}
    result["prompt_versions"] = {"A": PROMPT_VERSION, "C": REACT_VERSION}
    result["sample_sha256"] = _file_hash(SAMPLE_FILE)
    result["model_calls_this_command"] = {"new": llm.calls, "cached": llm.cache_hits, "providers": client.calls,
                                          "model_alone_sent": report.sent}
    result["openrouter_spend_usd"] = round(client.overflow.spent(), 4)
    names = {"A": "Model alone", "B": "Rules only", "C": "ReAct agent", "D": "Full system"}
    print(f"{args.part}: {len(cases)} cases ({result['clean_cases']} clean)")
    print(f"{'system':<14}{'fully correct':>14}{'recall':>9}{'precision':>11}{'$ recall':>10}{'false alarm (clean)':>21}")
    for key, name in names.items():
        sm = result["summaries"][key]
        det = sm["detection"]["all_checks"]
        cells = [det["recall"]["rate"], det["precision"]["rate"], sm["money"]["dollar_recall"]["rate"],
                 sm["false_alarm_rate_clean"]["rate"]]
        recall, precision, dollars, alarms = ("-" if v is None else f"{v:.2f}" for v in cells)
        print(f"{name:<14}{result['fully_correct_rate'][key]:>14}{recall:>9}{precision:>11}{dollars:>10}{alarms:>21}")
    for name, test in result["primary_tests"].items():
        print(f"{name}: {test}  (Holm p = {result['holm_adjusted'][name]})")
    print("ReAct operations:", result["agent_operations"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"m4_agent_{args.part}_{stamp}.json"
    out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(f"saved {out.relative_to(PROJECT_ROOT)}")


def cmd_score_abstention(args) -> None:
    """Part A2: grader abstention on unanswerable vs answerable questions (one model call per question)."""
    from bill_auditor.evaluation.abstention import absent_terms_found, evaluate, load_sets
    from bill_auditor.ingest.index_manual import open_chroma
    from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

    paths = load_paths()
    queries_dir = PROJECT_ROOT / "eval" / "retrieval_queries"
    unanswerable, answerable = load_sets(queries_dir / "abstention_queries.json", queries_dir / "queries.json")
    llm, client = _m4_llm(paths, args)
    with closing(connect(paths.sqlite_path)) as conn:
        present = absent_terms_found(conn, unanswerable)
        if present:
            print(f"Not unanswerable after all (terms found in the manual): {present}")
            return
        result = evaluate(conn, open_chroma(paths.chroma_dir), FastEmbedEmbedder(paths.models_dir), llm, unanswerable, answerable)
    result["model"] = llm.model
    result["model_calls"] = {"new": llm.calls, "cached": llm.cache_hits, "providers": client.calls}
    result["openrouter_spend_usd"] = round(client.overflow.spent(), 4)
    for key in ["true_abstention", "over_abstention", "kept_an_expected_section"]:
        r = result[key]
        print(f"{key}: {r['successes']}/{r['n']}")
    print(f"relevance floor abstained on {result['floor_abstained_unanswerable']} unanswerable; "
          f"invalid grader answers {result['invalid_grader_answers']}; calls {result['model_calls']}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"m4_abstention_{stamp}.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"saved {out.relative_to(PROJECT_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    run_p = sub.add_parser("run-baseline", help="answer cases with the model-alone baseline (resumable)")
    run_p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    run_p.add_argument("--max-requests", type=int, default=25, help="stop after this many API requests")
    run_p.add_argument("--min-interval", type=float, default=13.0, help="seconds between requests (free tier)")
    run_p.set_defaults(func=cmd_run)
    score_p = sub.add_parser("score", help="score saved outputs and write eval/results/")
    score_p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    score_p.set_defaults(func=cmd_score)
    rules_p = sub.add_parser("score-rules", help="run the rules-only graph on all cases and score it")
    rules_p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    rules_p.add_argument("--trace", action="store_true", help="write a local trace file per case")
    rules_p.set_defaults(func=cmd_score_rules)
    full_p = sub.add_parser("score-full", help="run the full system (free-tier model, cached) and score it")
    full_p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    full_p.add_argument("--max-calls", type=int, default=300, help="cap on new model calls in this run")
    full_p.add_argument("--daily-cap", type=int, default=450,
                        help="cap on model calls per day; Google's free tier for this model is 500/day (seen 2026-10-07)")
    full_p.add_argument("--min-interval", type=float, default=5.0, help="seconds between model calls")
    full_p.add_argument("--trace", action="store_true")
    full_p.set_defaults(func=cmd_score_full)
    retrieval_p = sub.add_parser("score-retrieval", help="Milestone 4: compare search modes on the labelled queries")
    retrieval_p.set_defaults(func=cmd_score_retrieval)
    react_p = sub.add_parser("run-react", help="Milestone 4: run the ReAct agent on the locked sample (resumable)")
    react_p.add_argument("--part", choices=["tuning", "dev_scored", "heldout_scored"], required=True)
    react_p.add_argument("--limit", type=int, default=None, help="run at most this many new cases")
    react_p.add_argument("--max-calls", type=int, default=400)
    react_p.add_argument("--daily-cap", type=int, default=2000, help="calls/day across providers (Google's own limit triggers the overflow)")
    react_p.add_argument("--min-interval", type=float, default=5.0)
    react_p.add_argument("--cap-usd", type=float, default=1.00, help="OpenRouter spend cap for all Milestone 4 runs")
    react_p.set_defaults(func=cmd_run_react)
    m4_p = sub.add_parser("score-m4", help="Milestone 4: score A, B, C, D on the same sample cases")
    m4_p.add_argument("--part", choices=["dev_scored", "heldout_scored"], required=True)
    m4_p.add_argument("--max-calls", type=int, default=100)
    m4_p.add_argument("--daily-cap", type=int, default=2000)
    m4_p.add_argument("--min-interval", type=float, default=5.0)
    m4_p.add_argument("--cap-usd", type=float, default=1.00)
    m4_p.set_defaults(func=cmd_score_m4)
    abst_p = sub.add_parser("score-abstention", help="Milestone 4 Part A2: grader abstention (about 46 model calls)")
    abst_p.add_argument("--max-calls", type=int, default=60)
    abst_p.add_argument("--daily-cap", type=int, default=2000)
    abst_p.add_argument("--min-interval", type=float, default=5.0)
    abst_p.add_argument("--cap-usd", type=float, default=1.00)
    abst_p.set_defaults(func=cmd_score_abstention)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
