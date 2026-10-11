"""Paired tests on hand-computable examples."""

import pytest

from bill_auditor.evaluation.stats import holm, mcnemar_exact, paired_bootstrap_diff, paired_permutation


def test_mcnemar_counts_only_disagreements():
    a = [True] * 10 + [True] * 6 + [False] * 4
    b = [True] * 10 + [False] * 6 + [False] * 4
    result = mcnemar_exact(a, b)
    assert (result["a_only"], result["b_only"]) == (6, 0)
    assert result["p_value"] == pytest.approx(2 / 64)  # 6 one-way discordant items: 2 * 0.5^6


def test_mcnemar_no_disagreement_is_p_one():
    assert mcnemar_exact([True, False], [True, False])["p_value"] == 1.0


def test_mcnemar_balanced_disagreement_is_not_significant():
    a = [True, False] * 5
    b = [False, True] * 5
    assert mcnemar_exact(a, b)["p_value"] == 1.0


def test_permutation_detects_a_consistent_shift_and_not_noise():
    a = [1.0] * 20
    b = [0.5] * 20
    assert paired_permutation(a, b, resamples=2000)["p_value"] < 0.001
    noise = paired_permutation([1.0, 0.0] * 10, [0.0, 1.0] * 10, resamples=2000)
    assert noise["mean_difference"] == 0 and noise["p_value"] > 0.5


def test_bootstrap_interval_contains_the_true_difference():
    items = [(1, 0)] * 30 + [(1, 1)] * 70  # A right on all, B on 70%
    result = paired_bootstrap_diff(items, lambda s: sum(a for a, _ in s) / len(s),
                                   lambda s: sum(b for _, b in s) / len(s), resamples=1000)
    assert result["difference"] == pytest.approx(0.3)
    assert result["low"] < 0.3 < result["high"]


def test_holm_adjustment():
    adjusted = holm({"x": 0.01, "y": 0.04, "z": 0.03})
    assert adjusted["x"] == pytest.approx(0.03)
    assert adjusted["z"] == pytest.approx(0.06)
    assert adjusted["y"] == pytest.approx(0.06)  # monotone: never below a smaller p's adjustment


def test_bootstrap_p_value_separates_clear_and_null_differences():
    items = list(range(40))
    same = paired_bootstrap_diff(items, lambda s: sum(s) / len(s), lambda s: sum(s) / len(s))
    clear = paired_bootstrap_diff(items, lambda s: sum(s) / len(s) + 5, lambda s: sum(s) / len(s))
    assert same["p_value"] == 1.0 and clear["p_value"] == 0.0
