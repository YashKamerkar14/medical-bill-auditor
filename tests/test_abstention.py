"""Part A2 query sets: sizes, no overlap, and the absent-term check."""

import sqlite3

from bill_auditor.config import PROJECT_ROOT
from bill_auditor.evaluation.abstention import ANSWERABLE_COUNT, absent_terms_found, load_sets

QUERIES = PROJECT_ROOT / "eval" / "retrieval_queries"


def test_sets_are_balanced_and_disjoint():
    unanswerable, answerable = load_sets(QUERIES / "abstention_queries.json", QUERIES / "queries.json")
    assert len(unanswerable) == ANSWERABLE_COUNT == len(answerable)
    assert not {q["id"] for q in unanswerable} & {q["id"] for q in answerable}
    assert all(q["expected_sections"] for q in answerable)


def test_absent_term_check_catches_a_term_that_is_present():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE manual_sections (title TEXT, text TEXT)")
    conn.execute("INSERT INTO manual_sections VALUES ('Modifiers', 'Ambulance services are not covered here.')")
    queries = [{"id": "u01", "absent_terms": ["ambulance"]}, {"id": "u02", "absent_terms": ["hospice"]}]
    assert absent_terms_found(conn, queries) == {"u01": ["ambulance"]}
