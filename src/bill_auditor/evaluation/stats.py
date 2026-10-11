"""Paired statistical tests for comparing two systems on the SAME items (Milestone 4).

Comparing two systems on the same bills or queries is a paired design: what
matters is where they disagree. Unpaired intervals on each system's rate
would waste that pairing and need far more data.

- mcnemar_exact: per-item correct/incorrect for A and B. Only discordant items
  (one right, the other wrong) carry information; under "no difference" each
  discordant item is a fair coin, so the p-value is an exact two-sided
  binomial test on the discordant counts.
- paired_permutation: per-item scores (e.g. reciprocal rank). Under "no
  difference" the sign of each paired difference is arbitrary, so flip signs
  at random and see how often the mean difference is as large as observed.
- paired_bootstrap_diff: interval for the difference of a ratio metric
  (precision, recall, dollar recall) by resampling ITEMS, with both systems
  kept together for each resampled item.
- holm: corrects p-values for the handful of comparisons chosen in advance as
  primary; everything else is reported as descriptive.
"""

import math
import random
from collections.abc import Callable, Sequence


def mcnemar_exact(a_correct: Sequence[bool], b_correct: Sequence[bool]) -> dict:
    if len(a_correct) != len(b_correct):
        raise ValueError("paired lists must have the same length")
    a_only = sum(a and not b for a, b in zip(a_correct, b_correct))
    b_only = sum(b and not a for a, b in zip(a_correct, b_correct))
    n = a_only + b_only
    if n == 0:
        return {"a_only": 0, "b_only": 0, "p_value": 1.0}
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {"a_only": a_only, "b_only": b_only, "p_value": min(1.0, 2 * tail)}


def paired_permutation(a_scores: Sequence[float], b_scores: Sequence[float], resamples: int = 10000,
                       seed: int = 0) -> dict:
    if len(a_scores) != len(b_scores):
        raise ValueError("paired lists must have the same length")
    diffs = [a - b for a, b in zip(a_scores, b_scores)]
    observed = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(resamples):
        flipped = sum(d if rng.random() < 0.5 else -d for d in diffs) / len(diffs)
        if abs(flipped) >= abs(observed) - 1e-12:
            extreme += 1
    return {"mean_difference": observed, "p_value": (extreme + 1) / (resamples + 1)}


def paired_bootstrap_diff(items: Sequence, metric_a: Callable[[Sequence], float | None],
                          metric_b: Callable[[Sequence], float | None], resamples: int = 2000,
                          seed: int = 0) -> dict:
    """95% interval for metric_a(items) - metric_b(items), resampling items with replacement."""
    rng = random.Random(seed)
    point_a, point_b = metric_a(items), metric_b(items)
    diffs = []
    for _ in range(resamples):
        sample = [items[rng.randrange(len(items))] for _ in items]
        a, b = metric_a(sample), metric_b(sample)
        if a is not None and b is not None:
            diffs.append(a - b)
    diffs.sort()
    if not diffs or point_a is None or point_b is None:
        return {"difference": None, "low": None, "high": None, "p_value": 1.0}
    # Two-sided bootstrap p-value: twice the smaller share of resampled differences on either side of zero.
    tail = min(sum(d <= 0 for d in diffs), sum(d >= 0 for d in diffs)) / len(diffs)
    return {"difference": point_a - point_b, "low": diffs[int(0.025 * len(diffs))],
            "high": diffs[int(0.975 * len(diffs)) - 1], "p_value": min(1.0, 2 * tail)}


def holm(p_values: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (step-down, monotone)."""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adjusted, running = {}, 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[name] = running
    return adjusted
