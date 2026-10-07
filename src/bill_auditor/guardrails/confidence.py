"""Confidence is assigned by rule, never reported by a model (PLAN.md, decision rules).

One table, so every confidence in a report can be traced to a line here.
"""

from bill_auditor.checks.models import Confidence

CONFIDENCE: dict[str, Confidence] = {
    # The bill asks the patient for more than the EOB says they owe: both figures are on paper.
    "liability.balance_exceeds_eob": "high",
    # Same code, date, provider and modifiers twice with no repeat modifier.
    "duplicate.identical_lines": "high",
    # Numbers that do not add up.
    "arithmetic.line": "high",
    "arithmetic.total_charges": "high",
    "arithmetic.balance_due": "high",
    # MUE: MAI 2 is an absolute per-day policy limit; MAI 1 and 3 can be exceeded with
    # documentation, so they mean "ask for documentation" (manual Chapter I, section V).
    "mue.mai_2": "high",
    "mue.mai_1": "medium",
    "mue.mai_3": "medium",
    # PTP: indicator 0 can never be bypassed; indicator 1 without a modifier might be a
    # missing modifier rather than a billing error.
    "ptp.indicator_0": "high",
    "ptp.indicator_1": "medium",
    # Price: a benchmark; the data are exact even though "too high" is a judgement.
    "price.ratio": "high",
}


def confidence_for(rule: str) -> Confidence:
    return CONFIDENCE[rule]
