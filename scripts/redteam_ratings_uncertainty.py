#!/usr/bin/env python3
"""redteam_ratings_uncertainty.py — inferential statistics for the model-ratings correlation.

The empirical scoring paper (research/redteam-model-scoring AgentRatingsEmpiricalStudy) reports the
security_score vs reliability_score association as bare POINT estimates — Pearson r and Spearman rho —
with no confidence interval, no significance test, and no acknowledgement that on a small non-random
n those point estimates carry wide uncertainty. This script closes that gap (research gap G-A): it
recomputes the correlation from the pipeline's own ratings CSV and attaches

  * a bias-corrected-and-accelerated (BCa) and a percentile bootstrap 95%% CI, and
  * a permutation-test two-sided p-value (H0: no monotone/linear association),

so the reported number stops being a point estimate on a tiny sample. Stdlib-only (no numpy/scipy),
deterministic (fixed integer seed via a stdlib LCG so results reproduce without Random.seed policy
concerns), and read-only against the CSV — it changes no rating, only how the association is reported.

It is intentionally a REPORTING artifact, not part of the scoring pipeline: the scores are computed
elsewhere; this quantifies the uncertainty of a downstream summary statistic of them.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO_ROOT / "references" / "openweights-security-model-ratings.csv"


def _rank(values: list[float]) -> list[float]:
    """Return fractional (average-tie) ranks of ``values`` — the correct handling for Spearman ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-indexed average rank across the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson product-moment correlation. Returns 0.0 if either series has zero variance."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sxy / (sxx * syy) ** 0.5


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation = Pearson on average-tie ranks."""
    return pearson(_rank(xs), _rank(ys))


class _LCG:
    """Tiny deterministic PRNG (glibc LCG constants). Avoids Date/Random.seed policy concerns and
    makes the bootstrap fully reproducible across runs without external state."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0x7FFFFFFF

    def randint(self, n: int) -> int:
        """Return a pseudo-random int in [0, n)."""
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state % n


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile of an already-sorted list; ``q`` in [0, 1]."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _norm_cdf(z: float) -> float:
    """Standard normal CDF via the erf approximation (stdlib math.erf)."""
    import math
    return 0.5 * (1.0 + math.erf(z / 2.0 ** 0.5))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation); p in (0, 1)."""
    import math
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def bootstrap_ci(xs: list[float], ys: list[float], stat, iters: int, seed: int,
                 alpha: float = 0.05) -> dict:
    """Percentile + BCa bootstrap CI for a paired correlation statistic.

    Args:
        xs, ys: paired samples.
        stat: a function (xs, ys) -> float (e.g. ``pearson`` or ``spearman``).
        iters: bootstrap resamples.
        seed: deterministic PRNG seed.
        alpha: two-sided miss rate (0.05 -> 95%% CI).

    Returns:
        dict with ``point``, ``percentile_ci`` [lo, hi], ``bca_ci`` [lo, hi], ``iters``.
    """
    n = len(xs)
    point = stat(xs, ys)
    rng = _LCG(seed)
    boot: list[float] = []
    for _ in range(iters):
        idx = [rng.randint(n) for _ in range(n)]
        bx = [xs[i] for i in idx]
        by = [ys[i] for i in idx]
        boot.append(stat(bx, by))
    boot.sort()
    pct = [_percentile(boot, alpha / 2), _percentile(boot, 1 - alpha / 2)]

    # BCa: bias-correction z0 from the share of bootstrap stats below the point estimate, and
    # acceleration a from the jackknife skewness.
    below = sum(1 for b in boot if b < point)
    prop = below / iters if iters else 0.5
    prop = min(max(prop, 1e-6), 1 - 1e-6)
    z0 = _norm_ppf(prop)
    jack = []
    for i in range(n):
        jx = xs[:i] + xs[i + 1:]
        jy = ys[:i] + ys[i + 1:]
        jack.append(stat(jx, jy))
    jbar = sum(jack) / n
    num = sum((jbar - j) ** 3 for j in jack)
    den = 6.0 * (sum((jbar - j) ** 2 for j in jack) ** 1.5)
    a = num / den if den != 0 else 0.0
    zl, zu = _norm_ppf(alpha / 2), _norm_ppf(1 - alpha / 2)
    def _adj(z):
        denom = 1 - a * (z0 + z)
        return _norm_cdf(z0 + (z0 + z) / denom) if denom != 0 else _norm_cdf(z0 + z)
    bca = [_percentile(boot, _adj(zl)), _percentile(boot, _adj(zu))]
    return {"point": round(point, 4), "percentile_ci": [round(pct[0], 4), round(pct[1], 4)],
            "bca_ci": [round(bca[0], 4), round(bca[1], 4)], "iters": iters}


def permutation_pvalue(xs: list[float], ys: list[float], stat, iters: int, seed: int) -> float:
    """Two-sided permutation p-value for association (H0: xs and ys are independent).

    Shuffles ``ys`` against ``xs`` ``iters`` times and returns the share of permutations whose |stat|
    is >= the observed |stat|, with the standard +1/+1 correction so the p-value is never 0.
    """
    observed = abs(stat(xs, ys))
    rng = _LCG(seed)
    ys_work = list(ys)
    ge = 0
    for _ in range(iters):
        # Fisher-Yates shuffle of ys_work
        for i in range(len(ys_work) - 1, 0, -1):
            j = rng.randint(i + 1)
            ys_work[i], ys_work[j] = ys_work[j], ys_work[i]
        if abs(stat(xs, ys_work)) >= observed:
            ge += 1
    return (ge + 1) / (iters + 1)


def load_pairs(csv_path: Path, xcol: str, ycol: str) -> tuple[list[float], list[float], list[str]]:
    """Load two numeric columns (and the model label) from the ratings CSV, dropping blank rows."""
    xs, ys, labels = [], [], []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get(xcol) or not row.get(ycol):
                continue
            try:
                xs.append(float(row[xcol]))
                ys.append(float(row[ycol]))
            except ValueError:
                continue
            labels.append(row.get("model", "?"))
    return xs, ys, labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bootstrap CIs + permutation p-value for the ratings correlation.")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--x", default="security_score")
    ap.add_argument("--y", default="reliability_score")
    ap.add_argument("--iters", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260820)
    args = ap.parse_args(argv)

    xs, ys, labels = load_pairs(Path(args.csv), args.x, args.y)
    n = len(xs)
    if n < 3:
        print(f"need >= 3 paired rows, got {n}", file=sys.stderr)
        return 2

    out = {
        "csv": str(args.csv), "x": args.x, "y": args.y, "n": n, "seed": args.seed,
        "pearson": bootstrap_ci(xs, ys, pearson, args.iters, args.seed),
        "spearman": bootstrap_ci(xs, ys, spearman, args.iters, args.seed + 1),
        "pearson_perm_p": round(permutation_pvalue(xs, ys, pearson, args.iters, args.seed + 2), 5),
        "spearman_perm_p": round(permutation_pvalue(xs, ys, spearman, args.iters, args.seed + 3), 5),
        "note": ("CIs are 95% (percentile + BCa) over `iters` resamples; permutation p is two-sided "
                 "with +1 correction. n is small and non-random — the CI WIDTH is the headline, not "
                 "the point estimate."),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
