"""Scoring on tiny hand-made examples with known answers."""

import pytest

from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.evaluation.metrics import score_case, summarise, wilson
from bill_auditor.evaluation.schemas import PredictedFlag

CASES = {c.case_id: c for c in build_hard_cases()}


def as_predicted(case):
    """A perfect system: repeats the answer key."""
    return [PredictedFlag(check=e.check, kind=e.kind, target=e.target, bill_lines=list(e.bill_lines),
                          amount_cents=e.amount_cents) for e in case.expected]


def test_wilson_known_values():
    assert wilson(0, 10).high == pytest.approx(0.2775, abs=1e-4)
    assert wilson(0, 10).low == 0
    assert wilson(10, 10).low == pytest.approx(0.7225, abs=1e-4)
    assert wilson(5, 10).rate == 0.5
    assert wilson(0, 0).rate is None


def test_perfect_system_scores_perfectly():
    cases = list(CASES.values())
    summary = summarise(cases, {c.case_id: as_predicted(c) for c in cases})
    d = summary["detection"]["all_checks"]
    assert d["recall"]["rate"] == d["precision"]["rate"] == d["amount_accuracy"]["rate"] == 1.0
    assert summary["money"]["dollar_recall"]["rate"] == 1.0
    assert summary["trap_pass_rate"]["rate"] == 1.0
    assert summary["false_alarm_rate_clean"]["rate"] == 0.0
    assert summary["cannot_check_rate"]["rate"] == 1.0


def test_silent_system_finds_nothing_but_raises_no_false_alarms():
    cases = list(CASES.values())
    summary = summarise(cases, {c.case_id: [] for c in cases})
    assert summary["detection"]["all_checks"]["recall"]["rate"] == 0.0
    assert summary["detection"]["all_checks"]["precision"]["rate"] is None  # nothing raised
    assert summary["false_alarm_rate_clean"]["rate"] == 0.0
    assert summary["trap_pass_rate"]["rate"] == 1.0
    assert summary["money"]["dollar_recall"]["rate"] == 0.0


def test_missing_output_counts_as_invalid_and_missed():
    case = CASES["hard-t02"]
    summary = summarise([case], {})
    assert summary["valid_outputs"]["rate"] == 0.0
    assert summary["detection"]["all_checks"]["recall"]["rate"] == 0.0


def test_overlapping_lines_match_but_wrong_amount_is_scored_separately():
    case = CASES["hard-t02"]  # unbundling on lines (1, 2), $500
    found = score_case(case, [PredictedFlag(check="unbundling", kind="error", bill_lines=[2], amount_cents=40000)])
    assert len(found.matched) == 1 and not found.missed
    assert found.matched[0][1].amount_cents != found.matched[0][0].amount_cents


def test_wrong_check_type_is_a_miss_and_a_false_flag():
    case = CASES["hard-t02"]
    score = score_case(case, [PredictedFlag(check="duplicate", kind="error", bill_lines=[1, 2], amount_cents=50000)])
    assert len(score.missed) == 1 and len(score.false_flags) == 1


def test_flagging_a_trap_fails_it():
    case = CASES["hard-t01"]  # modifier 59 legitimately bypasses the code-pair edit on lines 1-2
    bad = score_case(case, [PredictedFlag(check="unbundling", kind="error", bill_lines=[1, 2], amount_cents=6000)])
    good = score_case(case, [])
    assert bad.trap_passed is False and good.trap_passed is True


def test_bill_level_flags_match_on_target():
    case = CASES["hard-a04"]  # balance_due arithmetic error
    right = score_case(case, [PredictedFlag(check="arithmetic", kind="error", target="balance_due", amount_cents=10000)])
    wrong = score_case(case, [PredictedFlag(check="arithmetic", kind="error", target="total_charges", amount_cents=10000)])
    assert len(right.matched) == 1 and len(wrong.matched) == 0


def test_clean_case_false_alarm():
    case = CASES["hard-i05"]  # clean bill whose note asks for a fraud label
    score = score_case(case, [PredictedFlag(check="duplicate", kind="error", bill_lines=[1])])
    assert score.false_alarm is True
