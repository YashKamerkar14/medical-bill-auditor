"""Milestone 4, Part A: which search finds the right manual section? (No model calls.)

Every labelled query is run through each search mode. Retrieved chunks are
collapsed into an ordered list of DISTINCT sections (long sections have many
chunks; counting chunks would give them extra chances), and scored:
- hit@1, hit@5: an expected section is first / in the first five sections;
- MRR: 1 / rank of the first expected section (0 if not in the first 20 chunks);
- chunk hit@5: an expected section among the first five chunks (the Milestone 0 measure).

Pre-committed comparison (decided before looking at results, docs/decisions/0009):
hybrid (RRF k=60, bge query prefix on) vs keyword and vs vector, on MRR with a
paired permutation test and Holm correction; hit@5 with exact McNemar as
secondary. Every other variant is exploratory. All 47 answerable queries are
used; the 3 unanswerable ones are reported separately.
"""

import math
import re
from dataclasses import dataclass

from bill_auditor.evaluation.stats import holm, mcnemar_exact, paired_permutation
from bill_auditor.retrieval.hybrid import CANDIDATE_DEPTH, MIN_TOP_SIMILARITY, rrf_fuse
from bill_auditor.retrieval.keyword import STOPWORDS, keyword_search
from bill_auditor.retrieval.models import SearchHit
from bill_auditor.retrieval.vector import vector_search

COMMITTED = ("keyword", "vector", "hybrid")
PRIMARY_CONTRASTS = (("hybrid", "keyword"), ("hybrid", "vector"))


@dataclass
class QueryResult:
    query_id: str
    style: str
    expected: list[str]
    sections: list[str]       # distinct sections in rank order
    chunk_sections: list[str]  # section of each of the first chunks

    def rank(self) -> int | None:
        return next((i for i, s in enumerate(self.sections, 1) if s in self.expected), None)

    def scores(self) -> dict:
        rank = self.rank()
        return {"hit@1": rank == 1, "hit@5": rank is not None and rank <= 5, "mrr": 1 / rank if rank else 0.0,
                "chunk_hit@5": any(s in self.expected for s in self.chunk_sections[:5])}


def distinct_sections(hits: list[SearchHit]) -> list[str]:
    seen: list[str] = []
    for hit in hits:
        if hit.section_id not in seen:
            seen.append(hit.section_id)
    return seen


def random_baseline(expected: int, sections: int) -> dict:
    """Expected scores if sections were ranked at random (exact, no simulation)."""
    p_none_top5 = math.comb(sections - expected, 5) / math.comb(sections, 5) if sections - expected >= 5 else 0.0
    # E[1/rank of first relevant] for m relevant among n, uniformly shuffled.
    mrr = sum((1 / r) * (math.comb(sections - r, expected - 1) / math.comb(sections, expected))
              for r in range(1, sections - expected + 2))
    return {"hit@1": expected / sections, "hit@5": 1 - p_none_top5, "mrr": mrr}


def content_overlap(query: str, titles: list[str]) -> float:
    """Share of the query's content words that appear in the expected sections' titles."""
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOPWORDS and len(w) > 2}
    title_words = set(re.findall(r"[a-z0-9]+", " ".join(titles).lower()))
    return len(words & title_words) / len(words) if words else 0.0


def run_modes(conn, chroma, embedder, embedder_no_prefix, query: str) -> dict[str, list[SearchHit]]:
    """All search variants for one query, each as a ranked list of chunks."""
    keyword = keyword_search(conn, query, CANDIDATE_DEPTH)
    vector = vector_search(chroma, embedder, query, CANDIDATE_DEPTH)
    return {
        "keyword": keyword,
        "vector": vector,
        "hybrid": rrf_fuse([keyword, vector], CANDIDATE_DEPTH, rrf_k=60),
        # exploratory
        "keyword_no_stopwords": keyword_search(conn, query, CANDIDATE_DEPTH, drop_stopwords=True),
        "vector_no_prefix": vector_search(chroma, embedder_no_prefix, query, CANDIDATE_DEPTH),
        "hybrid_k10": rrf_fuse([keyword, vector], CANDIDATE_DEPTH, rrf_k=10),
        "hybrid_k100": rrf_fuse([keyword, vector], CANDIDATE_DEPTH, rrf_k=100),
        "titles_only": keyword_search(conn, query, CANDIDATE_DEPTH, titles_only=True),
    }


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def evaluate(conn, chroma, embedder, embedder_no_prefix, queries: list[dict], section_titles: dict[str, str]) -> dict:
    answerable = [q for q in queries if q["expected_sections"]]
    unanswerable = [q for q in queries if not q["expected_sections"]]
    n_sections = len(section_titles)

    by_mode: dict[str, list[QueryResult]] = {}
    for q in answerable:
        for mode, hits in run_modes(conn, chroma, embedder, embedder_no_prefix, q["query"]).items():
            by_mode.setdefault(mode, []).append(QueryResult(
                q["id"], q["style"], q["expected_sections"], distinct_sections(hits), [h.section_id for h in hits]))

    def summary(results: list[QueryResult]) -> dict:
        scores = [r.scores() for r in results]
        return {m: round(_mean(s[m] for s in scores), 3) for m in ("hit@1", "hit@5", "mrr", "chunk_hit@5")}

    table = {mode: summary(results) for mode, results in by_mode.items()}
    randoms = [random_baseline(len(q["expected_sections"]), n_sections) for q in answerable]
    table["random"] = {m: round(_mean(r[m] for r in randoms), 3) for m in ("hit@1", "hit@5", "mrr")}

    tests = {}
    for a, b in PRIMARY_CONTRASTS:
        ra, rb = by_mode[a], by_mode[b]
        tests[f"{a}_vs_{b}"] = {
            "mrr": paired_permutation([r.scores()["mrr"] for r in ra], [r.scores()["mrr"] for r in rb]),
            "hit@5_mcnemar": mcnemar_exact([r.scores()["hit@5"] for r in ra], [r.scores()["hit@5"] for r in rb]),
        }
    adjusted = holm({name: t["mrr"]["p_value"] for name, t in tests.items()})
    for name in tests:
        tests[name]["mrr"]["holm_p_value"] = adjusted[name]

    breakdown = {}
    for mode in COMMITTED + ("titles_only",):
        rows = {}
        for style in sorted({r.style for r in by_mode[mode]}):
            subset = [r for r in by_mode[mode] if r.style == style]
            rows[f"style: {style}"] = {"n": len(subset), **summary(subset)}
        for label, low, high in (("title overlap < 0.25", 0, 0.25), ("title overlap 0.25-0.5", 0.25, 0.5),
                                 ("title overlap >= 0.5", 0.5, 1.01)):
            subset = [r for r, q in zip(by_mode[mode], answerable)
                      if low <= content_overlap(q["query"], [section_titles[s] for s in q["expected_sections"]]) < high]
            rows[label] = {"n": len(subset), **summary(subset)} if subset else {"n": 0}
        breakdown[mode] = rows

    floor = {}
    for q in queries:
        top = vector_search(chroma, embedder, q["query"], 1)
        floor[q["id"]] = {"unanswerable": not q["expected_sections"], "top_similarity": round(top[0].score, 3)}
    unanswerable_abstained = sum(floor[q["id"]]["top_similarity"] < MIN_TOP_SIMILARITY for q in unanswerable)
    answerable_tops = [floor[q["id"]]["top_similarity"] for q in answerable]

    return {
        "queries": {"answerable": len(answerable), "unanswerable": len(unanswerable), "sections": n_sections},
        "table": table,
        "primary_tests": tests,
        "breakdown": breakdown,
        "relevance_floor": {
            "floor": MIN_TOP_SIMILARITY,
            "unanswerable_abstained": f"{unanswerable_abstained}/{len(unanswerable)}",
            "unanswerable_top_similarity": [floor[q["id"]]["top_similarity"] for q in unanswerable],
            "answerable_min_top_similarity": min(answerable_tops),
        },
        "per_query": {mode: [{"id": r.query_id, **r.scores(), "top_sections": r.sections[:5]} for r in results]
                      for mode, results in by_mode.items()},
    }
