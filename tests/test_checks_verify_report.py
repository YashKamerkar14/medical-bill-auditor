"""The six checks, the verify step and the report, on a fake reference database."""

from datetime import date

import pytest

from bill_auditor.checks import price
from bill_auditor.checks.context import CheckContext
from bill_auditor.checks.matching import LineMatch, match_lines
from bill_auditor.checks.models import Evidence
from bill_auditor.evaluation.hard_cases import build_hard_cases, hard, line
from bill_auditor.guardrails.verify import CHECKS, verify
from bill_auditor.parsing.intake import intake, unknown_lines
from bill_auditor.parsing.schemas import Bill
from bill_auditor.report.report import NOTICE, WordingViolation, build_report, render_markdown

CASES = {c.case_id: c for c in build_hard_cases()}


def context(conn, case) -> CheckContext:
    result = intake(conn, case.bill, case.eob)
    matches = (match_lines(result.bill, result.eob) if result.eob else
               [LineMatch(bill_line=l.line_no, eob_line=None, method="unmatched") for l in result.bill.lines])
    return CheckContext(conn=conn, bill=result.bill, eob=result.eob, matches=matches,
                        skip_lines=frozenset(unknown_lines(result)), table_checks=result.table_checks)


def all_flags(conn, case):
    ctx = context(conn, case)
    return [f for check in CHECKS.values() for f in check(ctx).flags]


def key(flags):
    return sorted((f.check, f.target, f.bill_lines, f.amount_cents) for f in flags)


def expected_key(case):
    return sorted((e.check, e.target, e.bill_lines, e.amount_cents) for e in case.expected if e.kind != "cannot_check")


# Only reading the descriptions and EOB remarks can pair these lines (Milestone 3, model-assisted matching).
NEEDS_MODEL = {"hard-m09", "hard-m11"}


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_hard_case_gives_exactly_the_expected_flags(audit_db, case_id):
    case = CASES[case_id]
    if case.bill.bill_type != "professional":
        pytest.skip("out of scope: no checks run")
    if case_id in NEEDS_MODEL:  # rules alone must abstain, never guess a pairing
        assert all_flags(audit_db, case) == []
        return
    assert key(all_flags(audit_db, case)) == expected_key(case)


def test_confidence_follows_the_rules(audit_db):
    by_case = {cid: all_flags(audit_db, CASES[cid]) for cid in ("hard-t02", "hard-t05", "hard-t12", "hard-a05")}
    assert [f.confidence for f in by_case["hard-t02"]] == ["high"]     # PTP indicator 0
    assert [f.confidence for f in by_case["hard-t05"]] == ["medium"]   # PTP indicator 1, no bypass modifier
    assert [f.confidence for f in by_case["hard-t12"]] == ["medium"]   # MUE MAI 3
    assert [f.confidence for f in by_case["hard-a05"]] == ["high"]     # arithmetic


def test_synthetic_rules_are_labelled_in_evidence_and_message(audit_db):
    (flag,) = all_flags(audit_db, CASES["hard-p01"])
    table = next(e for e in flag.evidence if e.source == "table_row")
    assert table.synthetic and table.release_label.startswith("SYNTHETIC")
    assert "synthetic code-pair edit" in flag.message


def _self_pay_bill(unit_price: float, code: str = "ZZ002") -> Bill:
    case = hard("x-price", ["test"], [line(1, code, unit_price)])
    provider = case.bill.provider.model_copy(update={"locality_id": "9999901"})
    return case.bill.model_copy(update={"provider": provider})


def test_price_benchmark_is_a_benchmark_not_an_error(audit_db):
    bill = _self_pay_bill(700.00)  # fake Medicare amount for ZZ002 locality 01 office: $200.50
    case = CASES["hard-t01"].model_copy(update={"bill": bill, "eob": None})
    (flag,) = price.run(context(audit_db, case)).flags
    assert (flag.kind, flag.amount_cents) == ("benchmark", 70000 - 20050)
    assert "not an error" in flag.message and "3.5 times" in flag.message


def test_price_under_the_threshold_is_not_reported(audit_db):
    case = CASES["hard-t01"].model_copy(update={"bill": _self_pay_bill(500.00), "eob": None})
    assert price.run(context(audit_db, case)).flags == []


def test_z_codes_have_no_price_and_are_cannot_check(audit_db):
    found = price.run(context(audit_db, CASES["hard-t01"]))
    assert found.flags == [] and {c.check for c in found.cannot} == {"price_benchmark"}


# --- verify --------------------------------------------------------------------------

def test_verify_keeps_honest_flags(audit_db):
    ctx = context(audit_db, CASES["hard-m08"])
    flags = all_flags(audit_db, CASES["hard-m08"])
    kept, dropped = verify(ctx, flags)
    assert kept == flags and dropped == []


def test_verify_drops_a_changed_amount(audit_db):
    ctx = context(audit_db, CASES["hard-t02"])
    (flag,) = all_flags(audit_db, CASES["hard-t02"])
    kept, dropped = verify(ctx, [flag.model_copy(update={"amount_cents": flag.amount_cents + 1})])
    assert kept == [] and "re-running the rule" in dropped[0].reason


def test_verify_drops_a_flag_no_rule_produces(audit_db):
    ctx = context(audit_db, CASES["hard-t01"])  # trap: modifier 59 bypasses the edit
    (honest,) = all_flags(audit_db, CASES["hard-t02"])
    fabricated = honest.model_copy(update={"bill_lines": (1, 2), "amount_cents": 6000})
    kept, dropped = verify(ctx, [fabricated])
    assert kept == [] and dropped


def test_verify_drops_evidence_that_does_not_match_the_database(audit_db):
    ctx = context(audit_db, CASES["hard-t02"])
    (flag,) = all_flags(audit_db, CASES["hard-t02"])
    table = next(e for e in flag.evidence if e.source == "table_row")
    forged = table.model_copy(update={"values": {**table.values, "modifier_indicator": "1"}})
    other = [e for e in flag.evidence if e is not table]
    kept, dropped = verify(ctx, [flag.model_copy(update={"evidence": (forged, *other)})])
    assert kept == [] and "does not match the database" in dropped[0].reason


def test_verify_drops_evidence_from_an_unloaded_release(audit_db):
    ctx = context(audit_db, CASES["hard-t02"])
    (flag,) = all_flags(audit_db, CASES["hard-t02"])
    fake = Evidence(source="table_row", table="ptp_edits", release_label="Made-up release", synthetic=True, values={})
    kept, dropped = verify(ctx, [flag.model_copy(update={"evidence": (fake,)})])
    assert kept == [] and "not loaded" in dropped[0].reason


# --- report --------------------------------------------------------------------------

def test_report_orders_sections_and_carries_notice_and_releases(audit_db):
    case = CASES["hard-p03"]
    flags = all_flags(audit_db, case)
    report = build_report(case.bill.bill_id, "completed", "none (rules only)", {"ptp": "SYNTHETIC x"}, flags, [], [], [], 10)
    text = render_markdown(report)
    assert report.notice == NOTICE and NOTICE in text
    assert text.index("Possible errors") < text.index("Price benchmarks") < text.index("Could not check")
    assert "[SYNTHETIC rule table]" in text and "SYNTHETIC x" in text
    assert [f.amount_cents for f in report.errors] == sorted([f.amount_cents for f in report.errors], reverse=True)


def test_report_refuses_accusatory_wording(audit_db):
    (flag,) = all_flags(audit_db, CASES["hard-t02"])
    bad = flag.model_copy(update={"message": "This provider committed fraud."})
    with pytest.raises(WordingViolation):
        build_report("B", "completed", "none", {}, [bad], [], [], [], 10)
