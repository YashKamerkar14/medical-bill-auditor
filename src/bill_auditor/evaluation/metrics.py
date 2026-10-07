"""Score a system's flags against the answer key.

Matching (docs/decisions/0006-evaluation-design.md): a predicted flag matches an
expected flag when the check, the kind and the target agree and, for line-level
flags, their bill lines overlap. Matching is one-to-one. The dollar amount is
scored separately (exact equality), so "found, wrong amount" and "missed" stay
distinct.

Every rate has a 95% Wilson interval. Dollar recall is a ratio of sums, so its
interval comes from a case-level bootstrap. Flags inside one case are not
independent, so the flag-level intervals are somewhat too narrow; case-level
rates (false alarms, traps) do not have that problem.
"""

import math
import random
from dataclasses import asdict, dataclass

from bill_auditor.evaluation.schemas import SYNTHETIC_RULE_CHECKS, Case, ExpectedFlag, PredictedFlag

REAL_RULE_CHECKS = ("provider_liability", "duplicate", "arithmetic")
Z = 1.96


@dataclass(frozen=True)
class Rate:
    successes: int
    n: int
    rate: float | None
    low: float | None
    high: float | None


def wilson(successes: int, n: int, z: float = Z) -> Rate:
    if n == 0:
        return Rate(successes, n, None, None, None)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Rate(successes, n, p, max(0.0, centre - half), min(1.0, centre + half))


def _matches(predicted: PredictedFlag, expected: ExpectedFlag) -> bool:
    if (predicted.check, predicted.kind, predicted.target) != (expected.check, expected.kind, expected.target):
        return False
    if expected.target == "line":
        return bool(set(predicted.bill_lines) & set(expected.bill_lines))
    return True


@dataclass
class CaseScore:
    case_id: str
    matched: list[tuple[ExpectedFlag, PredictedFlag]]
    missed: list[ExpectedFlag]
    false_flags: list[PredictedFlag]
    trap_passed: bool | None      # None when the case has no trap
    false_alarm: bool | None      # None when the case is not a clean case
    output_valid: bool


def is_clean_case(case: Case) -> bool:
    """Nothing to report at all: no expected flag of any kind and no trap."""
    return not case.expected and not case.traps


def score_case(case: Case, predicted: list[PredictedFlag] | None) -> CaseScore:
    """`predicted` None means the system produced no valid output for this case."""
    flags = list(predicted or [])
    remaining = list(flags)
    matched, missed = [], []
    for expected in case.expected:
        candidates = [p for p in remaining if _matches(p, expected)]
        # Prefer a candidate with exactly the expected lines, then any overlap.
        exact = [p for p in candidates if set(p.bill_lines) == set(expected.bill_lines)]
        choice = (exact or candidates or [None])[0]
        if choice is None:
            missed.append(expected)
        else:
            matched.append((expected, choice))
            remaining.remove(choice)
    false_flags = [p for p in remaining if p.kind in ("error", "benchmark")]

    trap_passed = None
    if case.traps:
        trap_passed = not any(
            p.kind == "error" and p.check == check and set(p.bill_lines) & set(lines)
            for check, lines in case.traps for p in flags
        )
    false_alarm = any(p.kind == "error" for p in flags) if is_clean_case(case) else None
    return CaseScore(case.case_id, matched, missed, false_flags, trap_passed, false_alarm, predicted is not None)


def _bootstrap_dollar_recall(scores: list[CaseScore], resamples: int = 1000, seed: int = 0) -> tuple[float | None, float | None]:
    per_case = []
    for s in scores:
        found = sum(e.amount_cents or 0 for e, _ in s.matched if e.kind == "error")
        total = found + sum(e.amount_cents or 0 for e in s.missed if e.kind == "error")
        per_case.append((found, total))
    if not any(total for _, total in per_case):
        return None, None
    rng = random.Random(seed)
    ratios = []
    for _ in range(resamples):
        sample = [per_case[rng.randrange(len(per_case))] for _ in per_case]
        total = sum(t for _, t in sample)
        if total:
            ratios.append(sum(f for f, _ in sample) / total)
    ratios.sort()
    return ratios[int(0.025 * len(ratios))], ratios[int(0.975 * len(ratios)) - 1]


def _detection(scores: list[CaseScore], checks: tuple[str, ...]) -> dict:
    found = sum(1 for s in scores for e, _ in s.matched if e.kind == "error" and e.check in checks)
    expected = found + sum(1 for s in scores for e in s.missed if e.kind == "error" and e.check in checks)
    raised = found + sum(1 for s in scores for p in s.false_flags if p.kind == "error" and p.check in checks)
    exact = sum(1 for s in scores for e, p in s.matched
                if e.kind == "error" and e.check in checks and p.amount_cents == e.amount_cents)
    return {
        "recall": asdict(wilson(found, expected)),
        "precision": asdict(wilson(found, raised)),
        "amount_accuracy": asdict(wilson(exact, found)),
    }


def summarise(cases: list[Case], outputs: dict[str, list[PredictedFlag] | None]) -> dict:
    """Metrics for every case in `cases`; a case missing from `outputs` counts as invalid output."""
    scores = [score_case(c, outputs.get(c.case_id)) for c in cases]
    all_checks = REAL_RULE_CHECKS + SYNTHETIC_RULE_CHECKS

    found_dollars = sum(e.amount_cents or 0 for s in scores for e, _ in s.matched if e.kind == "error")
    total_dollars = found_dollars + sum(e.amount_cents or 0 for s in scores for e in s.missed if e.kind == "error")
    low, high = _bootstrap_dollar_recall(scores)

    benchmark_found = sum(1 for s in scores for e, _ in s.matched if e.kind == "benchmark")
    benchmark_expected = benchmark_found + sum(1 for s in scores for e in s.missed if e.kind == "benchmark")
    benchmark_raised = benchmark_found + sum(1 for s in scores for p in s.false_flags if p.kind == "benchmark")
    cannot_found = sum(1 for s in scores for e, _ in s.matched if e.kind == "cannot_check")
    cannot_expected = cannot_found + sum(1 for s in scores for e in s.missed if e.kind == "cannot_check")
    traps = [s.trap_passed for s in scores if s.trap_passed is not None]
    alarms = [s.false_alarm for s in scores if s.false_alarm is not None]

    return {
        "cases": len(cases),
        "valid_outputs": asdict(wilson(sum(s.output_valid for s in scores), len(scores))),
        "detection": {
            "all_checks": _detection(scores, all_checks),
            "real_rule_checks": _detection(scores, REAL_RULE_CHECKS),
            "synthetic_rule_checks": _detection(scores, SYNTHETIC_RULE_CHECKS),
            "per_check": {check: _detection(scores, (check,)) for check in all_checks},
        },
        "money": {
            "dollar_recall": {"found_cents": found_dollars, "total_cents": total_dollars,
                              "rate": found_dollars / total_dollars if total_dollars else None, "low": low, "high": high},
        },
        "price_benchmark": {"recall": asdict(wilson(benchmark_found, benchmark_expected)),
                            "precision": asdict(wilson(benchmark_found, benchmark_raised))},
        "cannot_check_rate": asdict(wilson(cannot_found, cannot_expected)),
        "trap_pass_rate": asdict(wilson(sum(traps), len(traps))),
        "false_alarm_rate_clean": asdict(wilson(sum(alarms), len(alarms))),
    }


def format_report(summary: dict, title: str) -> str:
    """A plain-text table of the headline numbers."""
    def fmt(rate: dict) -> str:
        if rate.get("rate") is None:
            return "n/a"
        n = f" ({rate['successes']}/{rate['n']})" if "n" in rate else ""
        return f"{rate['rate']:.2f} [{rate['low']:.2f}-{rate['high']:.2f}]{n}"

    d = summary["detection"]
    rows = [
        ("Valid outputs", fmt(summary["valid_outputs"])),
        ("Error recall, all checks", fmt(d["all_checks"]["recall"])),
        ("Error precision, all checks", fmt(d["all_checks"]["precision"])),
        ("  recall, real-rule checks", fmt(d["real_rule_checks"]["recall"])),
        ("  recall, synthetic-rule checks", fmt(d["synthetic_rule_checks"]["recall"])),
        *[(f"  recall: {c}", fmt(v["recall"])) for c, v in d["per_check"].items()],
        ("Amount accuracy (matched flags)", fmt(d["all_checks"]["amount_accuracy"])),
        ("Dollar recall", fmt(summary["money"]["dollar_recall"])),
        ("Price benchmark recall", fmt(summary["price_benchmark"]["recall"])),
        ("Correct 'cannot check' rate", fmt(summary["cannot_check_rate"])),
        ("Trap pass rate", fmt(summary["trap_pass_rate"])),
        ("False alarm rate, clean bills", fmt(summary["false_alarm_rate_clean"])),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join([f"{title}  (cases: {summary['cases']})", "-" * (width + 40)]
                     + [f"{name:<{width}}  {value}" for name, value in rows])
