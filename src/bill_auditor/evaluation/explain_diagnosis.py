"""Milestone 4: why did some flags get "insufficient evidence"? (No model calls.)

The explain subgraph starts from a templated query (agent/explain.py,
`initial_query`): one per check type, filled with the rule's rationale or
indicator. This replays each flag's FIRST query from the Milestone 3 traces
through keyword, vector and hybrid search, and asks whether a section where the
rule is set out (baselines/full_system.py, `acceptable_sections`) is among the
first five distinct sections. Grouped by check type and by the explanation's
final status, it shows whether failures come from retrieval (the right section
never came back) or from grading and writing (it came back and was rejected).
"""

import json
from collections import defaultdict
from pathlib import Path

from bill_auditor.baselines.full_system import acceptable_sections
from bill_auditor.evaluation.retrieval_eval import distinct_sections
from bill_auditor.retrieval.hybrid import CANDIDATE_DEPTH, rrf_fuse
from bill_auditor.retrieval.keyword import keyword_search
from bill_auditor.retrieval.vector import vector_search


def explanations_from_traces(trace_dir: Path) -> list[dict]:
    """(case_id, flag_id, status, queries) per explained flag; the LAST run wins.

    Traces written before 2026-10-11 were appended across runs, so a case run on
    two days appears twice; keeping the last record per flag removes the repeats.
    """
    latest: dict[tuple[str, str], dict] = {}
    for path in sorted(trace_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            for flag_id, e in event.get("explanations", {}).items():
                if event["node"] == "verify_quotes" and e["queries"]:
                    latest[(path.stem, flag_id)] = {"case_id": path.stem, "flag_id": flag_id, "status": e["status"],
                                                    "queries": e["queries"], "rewrites": e["rewrites"]}
    return list(latest.values())


def diagnose(conn, chroma, embedder, explanations: list[dict], flags_by_case: dict[str, dict]) -> dict:
    rows = []
    for e in explanations:
        flag = flags_by_case.get(e["case_id"], {}).get(e["flag_id"])
        if flag is None:
            continue
        acceptable = acceptable_sections(flag)
        query = e["queries"][0]
        keyword = keyword_search(conn, query, CANDIDATE_DEPTH)
        vector = vector_search(chroma, embedder, query, CANDIDATE_DEPTH)
        found = {}
        for mode, hits in (("keyword", keyword), ("vector", vector), ("hybrid", rrf_fuse([keyword, vector], CANDIDATE_DEPTH))):
            found[mode] = bool(set(distinct_sections(hits)[:5]) & acceptable)
        rows.append({"check": flag.check, "status": e["status"], "rewrites": e["rewrites"], "query": query,
                     "acceptable": sorted(acceptable), **{f"{m}_top5": v for m, v in found.items()}})

    groups = defaultdict(list)
    for r in rows:
        groups[(r["check"], r["status"])].append(r)
    table = {f"{check} / {status}": {
        "n": len(g),
        **{f"{m}_top5": f"{sum(r[f'{m}_top5'] for r in g)}/{len(g)}" for m in ("keyword", "vector", "hybrid")},
        "distinct_first_queries": len({r["query"] for r in g}),
        "mean_rewrites": round(sum(r["rewrites"] for r in g) / len(g), 2),
    } for (check, status), g in sorted(groups.items())}
    return {"flags": len(rows), "table": table, "rows": rows}
