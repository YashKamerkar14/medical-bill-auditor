"""Hybrid search: Reciprocal Rank Fusion (RRF) of keyword and vector results.

RRF ignores raw scores (BM25 and cosine similarity are not comparable) and
uses ranks only: each chunk scores sum(1 / (RRF_K + rank)) over the lists it
appears in. A chunk ranked well by both searches rises to the top; one found
by only one still counts. RRF_K = 60 is the value from the original RRF paper
(Cormack et al., 2009) and the common default; Milestone 4 measures hybrid
against each search alone.

Relevance floor (CLAUDE.md, Evidence: "below a minimum relevance score, manual
search returns insufficient evidence"). Measured on the development queries
(2026-10-07): answerable queries had a top cosine similarity from 0.596 to 0.93,
the two unanswerable ones 0.633 and 0.667, so no threshold separates them.
The floor is therefore set just below the lowest answerable score and only
catches clearly off-topic searches; deciding whether passages actually answer
the question is the grader's job in the corrective-RAG loop.
"""

from dataclasses import dataclass

from bill_auditor.retrieval.keyword import keyword_search
from bill_auditor.retrieval.models import SearchHit
from bill_auditor.retrieval.vector import vector_search

RRF_K = 60
CANDIDATE_DEPTH = 20
MIN_TOP_SIMILARITY = 0.55


@dataclass
class HybridResult:
    hits: list[SearchHit]
    top_similarity: float | None
    insufficient: bool  # True: nothing relevant enough; report "insufficient evidence"


def rrf_fuse(rankings: list[list[SearchHit]], k: int, rrf_k: int = RRF_K) -> list[SearchHit]:
    """Fuse ranked lists by Reciprocal Rank Fusion; ties broken by chunk id for determinism."""
    scores: dict[str, float] = {}
    first_seen: dict[str, SearchHit] = {}
    for ranking in rankings:
        for hit in ranking:
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + hit.rank)
            first_seen.setdefault(hit.chunk_id, hit)
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))[:k]
    return [first_seen[cid].model_copy(update={"mode": "hybrid", "rank": i, "score": round(scores[cid], 6)})
            for i, cid in enumerate(ordered, start=1)]


def hybrid_search(conn, chroma, embedder, query: str, k: int = 5) -> HybridResult:
    keyword = keyword_search(conn, query, CANDIDATE_DEPTH)
    vector = vector_search(chroma, embedder, query, CANDIDATE_DEPTH)
    top = vector[0].score if vector else None
    if top is None or top < MIN_TOP_SIMILARITY:
        return HybridResult(hits=[], top_similarity=top, insufficient=True)
    return HybridResult(hits=rrf_fuse([keyword, vector], k), top_similarity=top, insufficient=False)
