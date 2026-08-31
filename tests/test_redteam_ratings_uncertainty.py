"""Tests for redteam_ratings_uncertainty.py — the ratings-correlation uncertainty artifact (gap G-A)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import redteam_ratings_uncertainty as u  # noqa: E402


def test_pearson_perfect_positive():
    assert abs(u.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    assert abs(u.pearson([1, 2, 3, 4], [8, 6, 4, 2]) + 1.0) < 1e-9


def test_pearson_zero_variance_returns_zero():
    assert u.pearson([1, 2, 3], [5, 5, 5]) == 0.0


def test_rank_handles_ties_with_average():
    # values [10, 20, 20, 40] -> ranks [1, 2.5, 2.5, 4]
    assert u._rank([10, 20, 20, 40]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_monotone_nonlinear_is_one():
    # strictly monotone but nonlinear -> Spearman 1.0, Pearson < 1.0
    xs = [1, 2, 3, 4, 5]
    ys = [1, 4, 9, 16, 25]
    assert abs(u.spearman(xs, ys) - 1.0) < 1e-9
    assert u.pearson(xs, ys) < 1.0


def test_bootstrap_ci_is_deterministic_and_brackets_point():
    xs = [1, 2, 3, 4, 5, 6, 7, 8]
    ys = [2, 1, 4, 3, 6, 5, 8, 7]
    a = u.bootstrap_ci(xs, ys, u.pearson, iters=2000, seed=42)
    b = u.bootstrap_ci(xs, ys, u.pearson, iters=2000, seed=42)
    assert a == b  # deterministic across runs (fixed LCG seed)
    assert a["percentile_ci"][0] <= a["point"] <= a["percentile_ci"][1]


def test_permutation_pvalue_small_for_strong_association():
    xs = list(range(20))
    ys = list(range(20))  # perfect association
    p = u.permutation_pvalue(xs, ys, u.pearson, iters=2000, seed=7)
    assert p < 0.01


def test_permutation_pvalue_large_for_no_association():
    xs = [1, 2, 3, 4, 5, 6, 7, 8]
    ys = [5, 5, 5, 5, 5, 5, 5, 5]  # zero variance -> stat 0, no association
    p = u.permutation_pvalue(xs, ys, u.pearson, iters=1000, seed=7)
    assert p > 0.5


@pytest.mark.skipif(not (REPO_ROOT / "references" / "openweights-security-model-ratings.csv").exists(), reason="redteam operational data removed for public release / absent in this checkout")
def test_main_on_live_ratings_csv_reproduces_project_point_estimates(capsys):
    # Guards gap G-A's own numbers: the point estimates must match the pipeline's published figures.
    # Post the 2026-08-20 nemotron data-quality fix (its failed 0-parseable ablated run corrected via
    # the retry), the correlation moved to r=0.599 / rho=0.611 (was 0.567 / 0.581 pre-fix).
    rc = u.main(["--iters", "500"])
    assert rc == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 24
    assert abs(out["pearson"]["point"] - 0.599) < 0.005
    assert abs(out["spearman"]["point"] - 0.611) < 0.005
