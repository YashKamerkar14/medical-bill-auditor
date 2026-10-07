"""Run the Milestone 0 search queries against the real index and record the results.

Milestone 0 only requires the results to be recorded; there is no pass bar on
relevance yet (retrieval quality is measured properly in Milestone 4). The
queries and their expected sections are in eval/retrieval_queries/m0_search_oracles.json,
written from the manual's contents before looking at any search output.
Results go to DATA_DIR/eval/results/ because they contain manual text.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bill_auditor.ingest.index_manual import open_chroma
from bill_auditor.retrieval.embeddings import FastEmbedEmbedder
from bill_auditor.retrieval.keyword import keyword_search
from bill_auditor.retrieval.vector import vector_search

pytestmark = pytest.mark.acceptance

ORACLE_FILE = Path(__file__).parents[2] / "eval" / "retrieval_queries" / "m0_search_oracles.json"
K = 5


def test_search_oracles_are_run_and_recorded(paths, real_db):
    queries = json.loads(ORACLE_FILE.read_text(encoding="utf-8"))["queries"]
    if not queries:
        pytest.skip(f"No queries written yet in {ORACLE_FILE.name}")
    assert len(queries) >= 10, f"Milestone 0 needs 10 search oracles, found {len(queries)}"

    known_sections = {row[0] for row in real_db.execute("SELECT section_id FROM manual_sections")}
    for q in queries:
        unknown = set(q["expected_sections"]) - known_sections
        assert not unknown, f"{q['id']}: expected sections {unknown} do not exist"

    embedder = FastEmbedEmbedder(paths.models_dir)
    chroma = open_chroma(paths.chroma_dir)
    records = []
    for q in queries:
        record = {"id": q["id"], "query": q["query"], "expected_sections": q["expected_sections"]}
        for mode, hits in (
            ("keyword", keyword_search(real_db, q["query"], K)),
            ("vector", vector_search(chroma, embedder, q["query"], K)),
        ):
            sections = [h.section_id for h in hits]
            first = next((i + 1 for i, s in enumerate(sections) if s in q["expected_sections"]), None)
            record[mode] = {
                "hit_at_5": first is not None,
                "first_relevant_rank": first,
                "results": [{"chunk_id": h.chunk_id, "score": round(h.score, 4)} for h in hits],
            }
        records.append(record)

    out_dir = paths.eval_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = out_dir / f"m0_search_{stamp}.json"
    out_file.write_text(
        json.dumps(
            {"embedding_model": embedder.model_name, "k": K, "release": hits[0].release_label if hits else None,
             "queries": records},
            indent=2,
        ),
        encoding="utf-8",
    )

    for mode in ("keyword", "vector"):
        found = sum(r[mode]["hit_at_5"] for r in records)
        print(f"{mode}: expected section in top {K} for {found}/{len(records)} queries")
    print(f"recorded to {out_file}")
