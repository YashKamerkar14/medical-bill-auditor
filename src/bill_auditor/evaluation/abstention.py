"""Milestone 4, Part A2: does the corrective-RAG grader abstain when the manual has no answer?

Each question goes through the explain subgraph's own retrieval (hybrid RRF) and
grader prompt; the question stands in for the finding. True abstention: no
passage kept for an unanswerable question. Over-abstention: no passage kept for
an answerable one. The relevance floor's own abstentions are counted apart.
"""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from bill_auditor.agent.explain import GRADE_SYSTEM, Grade, _passages
from bill_auditor.evaluation.metrics import wilson
from bill_auditor.retrieval.hybrid import hybrid_search

ANSWERABLE_COUNT = 23


def load_sets(abstention_file: Path, queries_file: Path) -> tuple[list[dict], list[dict]]:
    """(unanswerable, answerable) queries, as fixed in the abstention file's notes."""
    new = json.loads(abstention_file.read_text(encoding="utf-8"))["unanswerable"]
    labelled = json.loads(queries_file.read_text(encoding="utf-8"))["queries"]
    old = [{"id": q["id"], "query": q["query"], "absent_terms": []} for q in labelled if not q["expected_sections"]]
    answerable = sorted((q for q in labelled if q["expected_sections"]), key=lambda q: q["id"])[:ANSWERABLE_COUNT]
    return new + old, answerable


def absent_terms_found(conn: sqlite3.Connection, unanswerable: list[dict]) -> dict[str, list[str]]:
    """Terms that DO occur in the manual (should be empty: each topic was checked absent)."""
    text = " ".join(t.lower() for (t,) in conn.execute("SELECT title || ' ' || text FROM manual_sections"))
    found = {q["id"]: [t for t in q["absent_terms"] if t.lower() in text] for q in unanswerable}
    return {k: v for k, v in found.items() if v}


def grade_query(conn, chroma, embedder, llm, query: str) -> dict:
    found = hybrid_search(conn, chroma, embedder, query)
    if found.insufficient or not found.hits:
        return {"floor_abstained": True, "kept_sections": []}
    answer = llm.structured(GRADE_SYSTEM, f"Finding: {query}\n\nPASSAGES:\n{_passages(found.hits)}", Grade).parsed
    keep = set(answer.relevant_passage_ids) if answer else set()
    return {"floor_abstained": False, "valid": answer is not None,
            "kept_sections": sorted({h.section_id for h in found.hits if h.chunk_id in keep})}


def evaluate(conn, chroma, embedder, llm, unanswerable: list[dict], answerable: list[dict]) -> dict:
    rows = []
    for kind, queries in (("unanswerable", unanswerable), ("answerable", answerable)):
        for q in queries:
            graded = grade_query(conn, chroma, embedder, llm, q["query"])
            rows.append({"id": q["id"], "kind": kind, **graded,
                         "expected_sections": q.get("expected_sections", [])})
    un = [r for r in rows if r["kind"] == "unanswerable"]
    an = [r for r in rows if r["kind"] == "answerable"]
    kept_right = [r for r in an if set(r["kept_sections"]) & set(r["expected_sections"])]
    return {
        "true_abstention": asdict(wilson(sum(not r["kept_sections"] for r in un), len(un))),
        "floor_abstained_unanswerable": sum(r["floor_abstained"] for r in un),
        "over_abstention": asdict(wilson(sum(not r["kept_sections"] for r in an), len(an))),
        "kept_an_expected_section": asdict(wilson(len(kept_right), len(an))),
        "invalid_grader_answers": sum(r.get("valid") is False for r in rows),
        "rows": rows,
    }
