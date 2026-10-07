"""Hand-written hard cases: well-formed, fake codes only, numbers consistent except where an error is planted."""

import re

import pytest

from bill_auditor.config import PROJECT_ROOT
from bill_auditor.evaluation.generator import read_cases
from bill_auditor.evaluation.hard_cases import build_hard_cases

COMMITTED = PROJECT_ROOT / "eval" / "hard_cases" / "cases.jsonl"


@pytest.fixture(scope="module")
def cases():
    return build_hard_cases()


def test_size_ids_and_splits(cases):
    assert 40 <= len(cases) <= 50
    assert len({c.case_id for c in cases}) == len(cases)
    assert {c.split for c in cases} == {"dev", "heldout"}


def test_only_fake_codes(cases):
    for case in cases:
        for line in case.bill.lines:
            assert re.match(r"^(Z\d{4}|Y9001)$", line.code), (case.case_id, line.code)


def test_expected_flags_and_traps_point_at_real_lines(cases):
    for case in cases:
        numbers = {l.line_no for l in case.bill.lines}
        for flag in case.expected:
            assert set(flag.bill_lines) <= numbers, case.case_id
        for _, lines in case.traps:
            assert set(lines) <= numbers, case.case_id


def test_arithmetic_is_consistent_except_where_planted(cases):
    for case in cases:
        bill = case.bill
        planted = {f.target: f for f in case.expected if f.check == "arithmetic"}
        for line in bill.lines:
            diff = abs(line.charge_cents - line.units * line.unit_price_cents)
            line_flags = [f for f in case.expected if f.check == "arithmetic" and line.line_no in f.bill_lines]
            assert diff == (line_flags[0].amount_cents if line_flags else 0), (case.case_id, line.line_no)
        total_diff = abs(bill.total_charges_cents - sum(l.charge_cents for l in bill.lines))
        assert total_diff == (planted["total_charges"].amount_cents if "total_charges" in planted else 0), case.case_id
        balance_diff = abs(bill.balance_due_cents - (bill.total_charges_cents - bill.payments_cents - bill.adjustments_cents))
        assert balance_diff == (planted["balance_due"].amount_cents if "balance_due" in planted else 0), case.case_id


def test_insured_lines_post_every_dollar(cases):
    for case in cases:
        if not case.bill.is_insured:
            continue
        for line in case.bill.lines:
            assert line.charge_cents == line.insurance_paid_cents + line.adjustment_cents + line.patient_balance_cents


def test_injection_cases_contain_instructions_and_traps_expect_nothing_on_their_lines(cases):
    for case in cases:
        if case.tags[0].startswith("injection:"):
            text = " ".join([case.bill.notes] + [l.description for l in case.bill.lines]
                            + ([l.remark for l in case.eob.lines] if case.eob else []))
            assert re.search(r"(?i)assistant|ignore|instruction|reviewer|please report", text), case.case_id
        for check, lines in case.traps:
            assert not [f for f in case.expected if f.check == check and set(f.bill_lines) & set(lines)]


def test_committed_file_matches_the_definitions(cases):
    assert COMMITTED.exists(), "Run scripts/build_hard_cases.py"
    assert read_cases(COMMITTED) == cases, "eval/hard_cases is stale: run scripts/build_hard_cases.py"
