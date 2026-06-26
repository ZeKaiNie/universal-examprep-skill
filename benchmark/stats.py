#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Small-sample PAIRED statistics for the hallucination benchmark (stdlib only).

We deliberately avoid numpy/scipy so this runs anywhere Python 3.8+ runs (incl. a
plain Windows box with only Claude Code installed). Implements the protocol the
methodology calls for:

  * mcnemar()            — paired binary outcomes (e.g. hallucinated yes/no), the
                           right test because each item goes through BOTH arms.
  * paired_bootstrap_ci() — item-level bootstrap percentile CI for a rate/score delta.
  * cohen_kappa()        — judge-vs-human agreement, used as the calibration gate
                           before we trust any LLM-judged number.

Significance rule (see README): only call a difference "real" when the bootstrap CI
lower bound > 0 AND the McNemar p-value < 0.05. At tiny n, report descriptively and
state the power limit instead of over-claiming.
"""

import math
import random


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _chi2_sf_df1(x):
    """Survival function P(X > x) for a chi-square with 1 dof, via erfc (exact)."""
    if x <= 0:
        return 1.0
    return math.erfc(math.sqrt(x / 2.0))


def mcnemar(baseline, treatment, continuity=True):
    """Paired binary McNemar test.

    baseline, treatment: equal-length sequences of 0/1 (e.g. 1 == hallucinated).
    Only the discordant pairs (where the two arms disagree) carry information.
    Returns b, c (discordant counts), the statistic, and a df=1 chi-square p-value.
    """
    if len(baseline) != len(treatment):
        raise ValueError("baseline and treatment must be the same length (paired)")
    b = sum(1 for x, y in zip(baseline, treatment) if x == 1 and y == 0)  # baseline worse
    c = sum(1 for x, y in zip(baseline, treatment) if x == 0 and y == 1)  # treatment worse
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "statistic": 0.0, "p_value": 1.0,
                "note": "no discordant pairs — arms never disagreed"}
    stat = (abs(b - c) - (1 if continuity else 0)) ** 2 / n
    return {"b": b, "c": c, "statistic": stat, "p_value": _chi2_sf_df1(stat)}


def paired_bootstrap_ci(baseline, treatment, statistic=mean,
                        n_resamples=10000, alpha=0.05, seed=12345):
    """Percentile bootstrap CI for  statistic(treatment) - statistic(baseline).

    Resamples ITEM INDICES (so the pairing is preserved). `statistic` maps a list
    of per-item values to a scalar (default = mean). Returns (observed_delta, lo, hi).
    """
    if len(baseline) != len(treatment):
        raise ValueError("baseline and treatment must be the same length (paired)")
    n = len(baseline)
    observed = statistic(treatment) - statistic(baseline)
    if n == 0:
        return observed, float("nan"), float("nan")
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        deltas.append(statistic([treatment[i] for i in idx]) -
                      statistic([baseline[i] for i in idx]))
    deltas.sort()
    lo = deltas[max(0, int((alpha / 2) * n_resamples))]
    hi = deltas[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples) - 1)]
    return observed, lo, hi


def cohen_kappa(rater_a, rater_b):
    """Cohen's kappa for two raters labelling the same items (categorical).

    Used for the human(Siyun)-vs-LLM-judge calibration gate. NOTE: when one label
    dominates (most answers are 'grounded'), kappa under-reports agreement; the
    README also recommends Gwet's AC2, but kappa is the standard first number.
    """
    if len(rater_a) != len(rater_b):
        raise ValueError("raters must label the same number of items")
    n = len(rater_a)
    if n == 0:
        return float("nan")
    labels = set(rater_a) | set(rater_b)
    p_observed = sum(1 for x, y in zip(rater_a, rater_b) if x == y) / n
    p_expected = sum((sum(1 for x in rater_a if x == l) / n) *
                     (sum(1 for y in rater_b if y == l) / n) for l in labels)
    if p_expected >= 1.0:
        return 1.0
    return (p_observed - p_expected) / (1 - p_expected)


def significant(mcnemar_result, ci_lo, ci_hi):
    """The strict rule: CI excludes 0 AND McNemar p < 0.05."""
    return (mcnemar_result["p_value"] < 0.05) and (ci_lo > 0 or ci_hi < 0)


if __name__ == "__main__":
    # tiny self-demo
    base = [1, 1, 1, 0, 1, 1, 0, 1]   # hallucinated? baseline
    treat = [0, 0, 1, 0, 0, 0, 0, 1]  # hallucinated? skill
    print("McNemar:", mcnemar(base, treat))
    print("bootstrap delta CI (hallucination rate):",
          paired_bootstrap_ci(base, treat))
    print("kappa (perfect):", cohen_kappa([1, 0, 1, 1], [1, 0, 1, 1]))
