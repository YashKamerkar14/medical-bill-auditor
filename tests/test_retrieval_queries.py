"""The labelled retrieval set is well formed and consistent with the Milestone 0 queries."""

import json
import string

from bill_auditor.config import PROJECT_ROOT

QUERIES = PROJECT_ROOT / "eval" / "retrieval_queries"
# Every lettered section of Chapters I and XI, except the two "Reserved for future use" (I.Q, I.U).
SECTIONS = {f"{ch}.{letter}" for ch in ("I", "XI") for letter in string.ascii_uppercase[:23]} - {"I.Q", "I.U"}


def load(name):
    return json.loads((QUERIES / name).read_text(encoding="utf-8"))["queries"]


def test_fifty_unique_queries_with_valid_labels():
    queries = load("queries.json")
    assert len(queries) == 50
    assert len({q["id"] for q in queries}) == 50
    assert len({q["query"].lower() for q in queries}) == 50
    for q in queries:
        assert set(q["expected_sections"]) <= SECTIONS, q["id"]
        assert q["split"] in ("dev", "heldout")
        assert (q["style"] == "unanswerable") == (q["expected_sections"] == []), q["id"]


def test_split_and_mix():
    queries = load("queries.json")
    assert sum(q["split"] == "heldout" for q in queries) == 12
    styles = {q["style"] for q in queries}
    assert {"manual terms", "plain language", "codes", "unanswerable"} <= styles


def test_milestone_0_queries_are_included_unchanged():
    m0 = load("m0_search_oracles.json")
    current = {q["id"]: q for q in load("queries.json")}
    for i, old in enumerate(m0, start=1):
        new = current[f"q{i:02d}"]
        assert (new["query"], new["expected_sections"]) == (old["query"], old["expected_sections"])
