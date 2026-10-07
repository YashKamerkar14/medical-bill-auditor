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
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
