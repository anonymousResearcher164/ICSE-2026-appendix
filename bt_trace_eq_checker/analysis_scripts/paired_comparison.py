"""
paired_comparison.py

Compares the three description sources against one another on trace
similarity, and reports confidence intervals.

Why paired
----------
All three sources describe the SAME 50 behaviour trees, so the samples are
not independent. Treating them as independent would throw away the pairing
and give wider, weaker intervals. Every comparison here is per tree: for
each BT, the similarity achieved from one source minus that achieved from
another.

Why these statistics
--------------------
Trace similarity is bounded at 0 and 100 and strongly bimodal -- many
trees sit at exactly 100, many near 0 -- so normal-theory intervals and a
paired t-test are not justified. Instead:

  * Wilcoxon signed-rank for the hypothesis test, which assumes only that
    the paired differences are symmetric about their median;
  * percentile bootstrap for the confidence intervals, which assumes
    nothing about the shape at all;
  * Holm correction across the three comparisons, since testing all pairs
    of three sources is three tests on the same data;
  * Cliff's delta as a distribution-free effect size;
  * win/loss/tie counts with an exact binomial interval, which is the
    plainest statement of the result and needs no distributional argument.

Mean rather than median paired differences are reported because many trees
are reproduced perfectly by more than one source, and those ties drag the
median difference toward zero even where one source clearly wins.

Reproducibility
---------------
The bootstrap is seeded, so the intervals are reproducible to the digit.
RESAMPLES is a conventional choice, not a derived one; anything from
10,000 upward is stable at this sample size.

Usage
-----
    python3 paired_comparison.py
    python3 paired_comparison.py --json paired_comparison.json
"""

import argparse
import itertools
import json
import pathlib
import re

import numpy as np
from scipy import stats

# The checker's reports live in the appendix repository, not next to this
# script. Resolved from __file__ so the script works from any cwd; override
# with --reports-dir if the two repositories sit elsewhere.
DEFAULT_REPORTS_DIR = (pathlib.Path(__file__).resolve().parents[3]
                       / "MODEL-Final-appendix" / "MODELS-2026-appendix"
                       / "bt_trace_eq_checker")

SOURCES = [
    ("Our method", "report_our_methods.txt"),
    ("Human-authored", "report_human_authored.txt"),
    ("BTGenBot", "report_btgenbot.txt"),
]
RESAMPLES = 20_000
SEED = 0
CONFIDENCE = 0.95


def read(path: str) -> dict:
    """Per-entry average order-tolerant trace similarity, as a percentage."""
    out = {}
    text = pathlib.Path(path).read_text(encoding="utf-8")
    for block in re.split(r"(?=^Entry \d+: )", text, flags=re.M):
        entry = re.match(r"^Entry (\d+):", block)
        sim = re.search(r"Average order-tolerant similarity: ([\d.]+)%", block)
        if entry and sim:
            out[int(entry.group(1))] = float(sim.group(1))
    if not out:
        raise SystemExit(f"no similarity values found in {path}")
    return out


def bootstrap_ci(diffs, statistic=np.mean, resamples=RESAMPLES, seed=SEED):
    """Percentile bootstrap interval for `statistic` of the paired differences."""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diffs), size=(resamples, len(diffs)))
    draws = statistic(np.asarray(diffs)[idx], axis=1)
    half = (1 - CONFIDENCE) / 2 * 100
    return tuple(np.percentile(draws, [half, 100 - half]))


def cliffs_delta(a, b):
    a, b = np.asarray(a), np.asarray(b)
    greater = sum((x > b).sum() for x in a)
    less = sum((x < b).sum() for x in a)
    return (greater - less) / (len(a) * len(b))


def holm(pvalues):
    """Holm-Bonferroni adjusted p-values, order preserved."""
    order = np.argsort(pvalues)
    adjusted = np.empty(len(pvalues))
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, pvalues[i] * (len(pvalues) - rank)))
        adjusted[i] = running
    return adjusted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--json", help="also write the results as JSON")
    ap.add_argument("--reports-dir", type=pathlib.Path,
                    default=DEFAULT_REPORTS_DIR,
                    help="directory holding the report_*.txt files")
    args = ap.parse_args()

    data = {label: read(args.reports_dir / path) for label, path in SOURCES}
    shared = sorted(set.intersection(*[set(d) for d in data.values()]))
    if not shared:
        raise SystemExit("the reports share no entries")

    print(f"{len(shared)} behaviour trees, paired across {len(SOURCES)} sources")
    print(f"bootstrap: {RESAMPLES:,} resamples, seed {SEED}, "
          f"{CONFIDENCE:.0%} percentile intervals\n")

    print(f"  {'source':16} {'median':>7} {'mean':>7} {'=100%':>6} {'<50%':>6}")
    for label, d in data.items():
        v = np.array([d[k] for k in shared])
        print(f"  {label:16} {np.median(v):6.1f}% {v.mean():6.1f}% "
              f"{int((v == 100).sum()):6} {int((v < 50).sum()):6}")

    results, raw_p = [], []
    for (la, _), (lb, _) in itertools.combinations(SOURCES, 2):
        x = np.array([data[la][k] for k in shared])
        y = np.array([data[lb][k] for k in shared])
        diff = x - y
        wins, losses = int((diff > 0).sum()), int((diff < 0).sum())
        test = stats.wilcoxon(x, y, zero_method="wilcox", alternative="two-sided")
        lo, hi = stats.binomtest(wins, wins + losses).proportion_ci(
            confidence_level=CONFIDENCE)
        results.append({
            "a": la, "b": lb,
            "mean_difference": float(diff.mean()),
            "mean_ci": [float(v) for v in bootstrap_ci(diff)],
            "median_difference": float(np.median(diff)),
            "median_ci": [float(v) for v in bootstrap_ci(diff, np.median)],
            "wins": wins, "losses": losses, "ties": len(diff) - wins - losses,
            "win_rate": wins / (wins + losses),
            "win_rate_ci": [float(lo), float(hi)],
            "wilcoxon_p": float(test.pvalue),
            "cliffs_delta": float(cliffs_delta(x, y)),
        })
        raw_p.append(test.pvalue)

    for r, p_adj in zip(results, holm(raw_p)):
        r["wilcoxon_p_holm"] = float(p_adj)
        print(f"\n{r['a']} vs {r['b']}")
        print(f"  mean difference   {r['mean_difference']:+6.1f} pp   "
              f"{CONFIDENCE:.0%} CI [{r['mean_ci'][0]:+.1f}, {r['mean_ci'][1]:+.1f}]")
        print(f"  median difference {r['median_difference']:+6.1f} pp   "
              f"{CONFIDENCE:.0%} CI [{r['median_ci'][0]:+.1f}, {r['median_ci'][1]:+.1f}]")
        print(f"  wins/losses/ties  {r['wins']}/{r['losses']}/{r['ties']}   "
              f"first source wins {r['win_rate']:.0%} of decided pairs, "
              f"CI [{r['win_rate_ci'][0]:.0%}, {r['win_rate_ci'][1]:.0%}]")
        print(f"  Wilcoxon p={r['wilcoxon_p']:.2e} "
              f"(Holm-adjusted {r['wilcoxon_p_holm']:.2e})   "
              f"Cliff's delta {r['cliffs_delta']:+.2f}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({
            "n_trees": len(shared), "resamples": RESAMPLES, "seed": SEED,
            "confidence": CONFIDENCE, "comparisons": results,
        }, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
