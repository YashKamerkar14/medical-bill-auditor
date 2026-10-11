"""Retrieval-experiment scoring helpers (Milestone 4, Part A)."""

import itertools

import pytest

from bill_auditor.evaluation.retrieval_eval import QueryResult, content_overlap, distinct_sections, random_baseline
from bill_auditor.retrieval.keyword import to_fts_query
from bill_auditor.retrieval.models import SearchHit


def hit(chunk_id, section):
    return SearchHit(chunk_id=chunk_id, section_id=section, section_title="t", page_start="I-1", page_end="I-1",
                     text="x", mode="keyword", rank=1, score=1.0, release_label="r")


def test_chunks_collapse_to_distinct_sections_in_order():
    hits = [hit("a#1", "I.E"), hit("a#2", "I.E"), hit("b#1", "I.V"), hit("a#3", "I.E"), hit("c#1", "I.A")]
    assert distinct_sections(hits) == ["I.E", "I.V", "I.A"]


def test_scores_use_the_first_expected_section():
    r = QueryResult("q", "codes", ["I.V"], ["I.E", "I.A", "I.V"], ["I.E", "I.E", "I.A", "I.V"])
    assert r.scores() == {"hit@1": False, "hit@5": True, "mrr": pytest.approx(1 / 3), "chunk_hit@5": True}
    miss = QueryResult("q", "codes", ["XI.U"], ["I.E"], ["I.E"])
    assert miss.scores()["mrr"] == 0.0 and not miss.scores()["hit@5"]


@pytest.mark.parametrize(("expected", "sections"), [(1, 10), (2, 10), (3, 12)])
def test_random_baseline_matches_brute_force(expected, sections):
    relevant = set(range(expected))
    perms = list(itertools.permutations(range(sections), 5))  # top-5 sequences, all equally likely
    hit5 = sum(bool(relevant & set(p)) for p in perms) / len(perms)
    first = [next((i + 1 for i, s in enumerate(p) if s in relevant), None) for p in perms]
    hit1 = sum(f == 1 for f in first) / len(perms)
    result = random_baseline(expected, sections)
    assert result["hit@5"] == pytest.approx(hit5)
    assert result["hit@1"] == pytest.approx(hit1)


def test_random_mrr_for_one_relevant_section_is_mean_of_reciprocals():
    assert random_baseline(1, 4)["mrr"] == pytest.approx((1 + 1 / 2 + 1 / 3 + 1 / 4) / 4)


def test_title_overlap_ignores_stopwords():
    assert content_overlap("What does an MUE adjudication indicator mean?", ["Medically Unlikely Edits (MUEs)"]) == 0.0
    # content words: add, codes, reported, alone ("on" is too short); 2 of 4 are in the title
    assert content_overlap("Add-on codes reported alone", ["Add-on Codes"]) == pytest.approx(2 / 4)


def test_keyword_variants_build_safe_queries():
    assert to_fts_query("Can they bill the panel", drop_stopwords=True) == '"bill" OR "panel"'
    assert to_fts_query("the of", drop_stopwords=True) == '"the" OR "of"'  # never empty: falls back to all words
    assert to_fts_query("lab panel", titles_only=True) == '{section_title} : ("lab" OR "panel")'
