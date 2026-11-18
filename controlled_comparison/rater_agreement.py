"""
rater_agreement.py

Inter-rater agreement for the Cycle 6 quality ratings, across the Prompt
Engineer and the two Independent Evaluators, on all five metrics.

What it reports, and why each is here
-------------------------------------
Krippendorff's alpha (ordinal)
    The headline. It takes all three raters at once instead of averaging
    pairwise numbers, is defined when the marginals are concentrated, and
    its ordinal difference function charges more for a 4-vs-1 disagreement
    than a 4-vs-3 one.

Cohen's kappa, pairwise, three weightings
    Unweighted, linear and quadratic. Quadratic matches the convention in
    the sheet's KappaCalc tab so the numbers are comparable with the
    existing cycles. Linear is reported alongside because quadratic treats
    a 4-vs-1 gap as nine times worse than 4-vs-3, which is a strong claim
    on a four-point scale, and the two diverge when most disagreements are
    adjacent -- as they are here.

Gwet's AC2 (ordinal weights)
    A robustness check. These ratings are heavily ceiling-weighted, and
    under concentrated marginals expected agreement approaches observed
    agreement, which drives kappa down or negative even when raters plainly
    agree -- the "kappa paradox". AC2 is built for that case. Where AC2 and
    kappa disagree sharply, the skew is doing the work, not the raters.

Raw and within-one agreement
    Uncorrected for chance, and reported precisely because the corrected
    coefficients understate practical agreement on skewed ordinal data.

Disagreement profile
    The distribution of |difference| per pair, and the items with a gap of
    two or more. A coefficient says how much raters disagreed; this says
    where, which is the part you can act on.

Unanimity and range, across all three raters at once
    Raw agreement's complement -- the share of descriptions on which at
    least one rater chose a different level -- together with the spread of
    per-item ranges (highest rating minus lowest). The complement alone
    overstates the problem, since one rater sitting a single level away
    from the other two counts the same as a three-level split. The range
    separates adjacent-category noise from genuine divergence, so both are
    reported together.

Run
---
    python3 rater_agreement.py                 # read the sheet
    python3 rater_agreement.py --csv cycle6.csv
    python3 rater_agreement.py --json out.json
    python3 rater_agreement.py --selftest      # check the estimators
"""

import argparse
import csv
import io
import itertools
import json
import pathlib
import sys
import urllib.request
from collections import Counter

SHEET_ID = "1dFsFHVwUJwyIzfKWSGMjw5ZhkZF0lp-cDpTPyc932eo"
GID = "715791249"
CSV_URL = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
           f"/export?format=csv&gid={GID}")

METRICS = ["Atomicity", "Ambiguity", "Completeness",
           "Understandability", "Consistency"]
COLUMN_BLOCKS = {"Prompt Engineer": 2,
                 "Independent Evaluator 1": 9,
                 "Independent Evaluator 2": 16}
SHORT = {"Prompt Engineer": "PE",
         "Independent Evaluator 1": "IE1",
         "Independent Evaluator 2": "IE2"}
CATEGORIES = [1.0, 2.0, 3.0, 4.0]


# --------------------------------------------------------------------------- #
# Estimators
# --------------------------------------------------------------------------- #

def _weight(i, j, k, kind):
    """Agreement weight between category positions i and j, k categories."""
    d = abs(i - j) / (k - 1)
    if kind == "unweighted":
        return 1.0 if i == j else 0.0
    if kind == "linear":
        return 1.0 - d
    if kind == "quadratic":
        return 1.0 - d * d
    raise ValueError(kind)


def cohen_kappa(x, y, kind="linear", categories=None):
    cats = categories or sorted(set(x) | set(y))
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    if k < 2:
        return float("nan")          # one category in play: kappa is 0/0
    n = len(x)
    obs = [[0] * k for _ in range(k)]
    for a, b in zip(x, y):
        obs[idx[a]][idx[b]] += 1
    rows = [sum(r) for r in obs]
    cols = [sum(c) for c in zip(*obs)]
    po = pe = 0.0
    for i in range(k):
        for j in range(k):
            w = _weight(i, j, k, kind)
            po += w * obs[i][j] / n
            pe += w * rows[i] * cols[j] / (n * n)
    return float("nan") if abs(1 - pe) < 1e-12 else (po - pe) / (1 - pe)


def krippendorff_alpha(units, categories=None):
    """
    Ordinal alpha. `units` is a list of rating lists, one per item.

    Uses the coincidence-matrix formulation: the ordinal difference between
    two categories is built from the marginals of that matrix, so how much a
    disagreement costs depends on how the scale was actually used.
    """
    cats = categories or sorted({v for u in units for v in u})
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    coin = [[0.0] * k for _ in range(k)]
    for unit in units:
        m = len(unit)
        if m < 2:
            continue                  # a single rating pairs with nothing
        for a, b in itertools.permutations(unit, 2):
            coin[idx[a]][idx[b]] += 1.0 / (m - 1)
    marg = [sum(row) for row in coin]
    total = sum(marg)
    if total <= 1:
        return float("nan")

    def delta2(i, j):
        lo, hi = (i, j) if i <= j else (j, i)
        inner = sum(marg[lo:hi + 1]) - (marg[i] + marg[j]) / 2.0
        return inner * inner

    d_obs = sum(coin[i][j] * delta2(i, j) for i in range(k) for j in range(k))
    d_exp = sum(marg[i] * marg[j] * delta2(i, j)
                for i in range(k) for j in range(k))
    if d_exp == 0:
        return float("nan")           # no variation at all
    d_obs /= total
    d_exp /= total * (total - 1)
    return 1.0 - d_obs / d_exp


def gwet_ac2(units, categories=None, kind="quadratic"):
    """
    Gwet's AC2 for any number of raters per item, with ordinal weights.

    Chance agreement is modelled from how often each category is used
    overall, not from the product of two raters' marginals, which is what
    keeps it stable when nearly every rating is the same value.
    """
    cats = categories or sorted({v for u in units for v in u})
    idx = {c: i for i, c in enumerate(cats)}
    q = len(cats)
    if q < 2:
        return float("nan")
    w = [[_weight(i, j, q, kind) for j in range(q)] for i in range(q)]

    usable = [u for u in units if len(u) >= 2]
    n = len(usable)
    if not n:
        return float("nan")

    pa = 0.0
    pi = [0.0] * q
    for unit in usable:
        r = len(unit)
        counts = [0] * q
        for v in unit:
            counts[idx[v]] += 1
        weighted = [sum(w[a][b] * counts[b] for b in range(q)) for a in range(q)]
        pa += sum(counts[a] * (weighted[a] - 1) for a in range(q)) / (r * (r - 1))
        for a in range(q):
            pi[a] += counts[a] / r
    pa /= n
    pi = [p / n for p in pi]
    tw = sum(w[a][b] for a in range(q) for b in range(q))
    pe = (tw / (q * (q - 1))) * sum(p * (1 - p) for p in pi)
    return float("nan") if abs(1 - pe) < 1e-12 else (pa - pe) / (1 - pe)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

def load(csv_path=None):
    if csv_path:
        text = pathlib.Path(csv_path).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(CSV_URL, timeout=60) as r:
            text = r.read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[2]
    for rater, base in COLUMN_BLOCKS.items():
        found = header[base + 1:base + 1 + len(METRICS)]
        if found != METRICS:
            raise SystemExit(f"columns for {rater} are {found}, expected "
                             f"{METRICS}; the sheet layout changed")
    data = [r for r in rows[3:] if r and r[0].strip()]
    ids = [r[0].strip() for r in data]
    ratings = {}
    for rater, base in COLUMN_BLOCKS.items():
        for i, metric in enumerate(METRICS):
            col = []
            for row in data:
                cell = row[base + 1 + i].strip() if len(row) > base + 1 + i else ""
                col.append(float(cell) if cell else None)
            ratings[(rater, metric)] = col
    return ids, ratings


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def fmt(x):
    return " n/a " if x != x else f"{x:+.3f}"


def analyse(ids, ratings):
    out = {}
    for metric in METRICS:
        per_rater = {r: ratings[(r, metric)] for r in COLUMN_BLOCKS}
        units, kept = [], []
        for i in range(len(ids)):
            vals = [per_rater[r][i] for r in COLUMN_BLOCKS
                    if per_rater[r][i] is not None]
            if len(vals) >= 2:
                units.append(vals)
                kept.append(i)

        ranges = [int(max(u) - min(u)) for u in units]
        n_u = len(ranges) or 1
        entry = {
            "n_items": len(units),
            "unanimous": sum(1 for r in ranges if r == 0) / n_u,
            "disagreement": sum(1 for r in ranges if r > 0) / n_u,
            "contested": sum(1 for r in ranges if r >= 2) / n_u,
            "mean_range": sum(ranges) / n_u,
            "range_profile": {d: ranges.count(d) for d in range(4)},
            "alpha_ordinal": krippendorff_alpha(units, CATEGORIES),
            "ac2_quadratic": gwet_ac2(units, CATEGORIES, "quadratic"),
            "pairs": {},
        }
        for a, b in itertools.combinations(COLUMN_BLOCKS, 2):
            xs, ys, items = [], [], []
            for i in range(len(ids)):
                p, q = per_rater[a][i], per_rater[b][i]
                if p is not None and q is not None:
                    xs.append(p); ys.append(q); items.append(ids[i])
            diffs = [abs(p - q) for p, q in zip(xs, ys)]
            entry["pairs"][f"{SHORT[a]} vs {SHORT[b]}"] = {
                "n": len(xs),
                "exact": sum(1 for d in diffs if d == 0) / len(xs),
                "within1": sum(1 for d in diffs if d <= 1) / len(xs),
                "kappa_unweighted": cohen_kappa(xs, ys, "unweighted", CATEGORIES),
                "kappa_linear": cohen_kappa(xs, ys, "linear", CATEGORIES),
                "kappa_quadratic": cohen_kappa(xs, ys, "quadratic", CATEGORIES),
                "diff_profile": {int(d): c for d, c in
                                 sorted(Counter(diffs).items())},
                "gap_ge_2": sorted(
                    ({"item": it, SHORT[a]: int(p), SHORT[b]: int(q),
                      "gap": int(abs(p - q))}
                     for it, p, q in zip(items, xs, ys) if abs(p - q) >= 2),
                    key=lambda e: -e["gap"]),
            }
        out[metric] = entry
    return out


def report(result):
    print(f"{'metric':18} {'n':>3} {'alpha(ord)':>11} {'AC2(quad)':>10}")
    for metric, e in result.items():
        print(f"{metric:18} {e['n_items']:3} {fmt(e['alpha_ordinal']):>11} "
              f"{fmt(e['ac2_quadratic']):>10}")

    print(f"\nAll three raters per description "
          f"(range = highest rating minus lowest)")
    print(f"  {'attribute':18} {'unanim':>7} {'disagree':>9} {'contested':>10} "
          f"{'mean rng':>9}   ranges 0/1/2/3")
    pooled = [0, 0, 0, 0]
    for metric, e in result.items():
        prof = "/".join(str(e["range_profile"][d]) for d in range(4))
        for d in range(4):
            pooled[d] += e["range_profile"][d]
        print(f"  {metric:18} {e['unanimous']:7.0%} {e['disagreement']:9.0%} "
              f"{e['contested']:10.0%} {e['mean_range']:9.2f}   {prof}")
    total = sum(pooled) or 1
    print(f"  {'pooled':18} {pooled[0]/total:7.0%} "
          f"{(total-pooled[0])/total:9.0%} "
          f"{(pooled[2]+pooled[3])/total:10.0%} "
          f"{sum(d*c for d, c in enumerate(pooled))/total:9.2f}   "
          + "/".join(str(c) for c in pooled))

    for metric, e in result.items():
        print(f"\n{metric}")
        print(f"  {'pair':14} {'n':>3} {'exact':>6} {'<=1':>6} "
              f"{'k unw':>7} {'k lin':>7} {'k quad':>7}   diffs 0/1/2/3")
        for pair, p in e["pairs"].items():
            prof = "/".join(str(p["diff_profile"].get(d, 0)) for d in range(4))
            print(f"  {pair:14} {p['n']:3} {p['exact']:6.0%} {p['within1']:6.0%} "
                  f"{fmt(p['kappa_unweighted']):>7} {fmt(p['kappa_linear']):>7} "
                  f"{fmt(p['kappa_quadratic']):>7}   {prof}")
        gaps = {pair: p["gap_ge_2"] for pair, p in e["pairs"].items()
                if p["gap_ge_2"]}
        if gaps:
            for pair, entries in gaps.items():
                shown = ", ".join(
                    f"{g['item']} ({list(g.values())[1]}/{list(g.values())[2]})"
                    for g in entries)
                print(f"    gap >= 2, {pair}: {shown}")


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def selftest() -> int:
    checks, failed = [], 0

    def check(label, got, want, tol=1e-6):
        nonlocal failed
        ok = (got != got and want != want) or abs(got - want) <= tol
        checks.append((label, got, want, ok))
        if not ok:
            failed += 1

    perfect = [[c, c] for c in (1.0, 2.0, 3.0, 4.0)] * 5
    check("alpha, perfect agreement", krippendorff_alpha(perfect, CATEGORIES), 1.0)
    check("AC2, perfect agreement", gwet_ac2(perfect, CATEGORIES), 1.0)
    x = [c for c, _ in perfect]
    check("kappa, perfect agreement",
          cohen_kappa(x, x, "unweighted", CATEGORIES), 1.0)

    # balanced 2x2 with independent raters: kappa is exactly 0
    a = [1.0, 1.0, 2.0, 2.0]
    b = [1.0, 2.0, 1.0, 2.0]
    check("kappa, chance-level 2x2", cohen_kappa(a, b, "unweighted", [1.0, 2.0]), 0.0)

    # a disagreement one step apart must score above one three steps apart
    near = cohen_kappa([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 3.0], "linear", CATEGORIES)
    far = cohen_kappa([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 1.0], "linear", CATEGORIES)
    checks.append(("linear kappa penalises distance", near, far, near > far))
    if not near > far:
        failed += 1

    # no variation at all: every coefficient is undefined, not 1
    flat = [[4.0, 4.0]] * 10
    check("alpha, zero variance", krippendorff_alpha(flat, CATEGORIES), float("nan"))

    for label, got, want, ok in checks:
        g = f"{got:+.4f}" if isinstance(got, float) and got == got else str(got)
        w = f"{want:+.4f}" if isinstance(want, float) and want == want else str(want)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:36} got {g:>9}  want {w}")
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Inter-rater agreement, Cycle 6")
    ap.add_argument("--csv", help="read a saved CSV export instead of the sheet")
    ap.add_argument("--json", help="also write the full results as JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="verify the estimators on cases with known answers")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    ids, ratings = load(args.csv)
    result = analyse(ids, ratings)
    print(f"{len(ids)} items x {len(COLUMN_BLOCKS)} raters x "
          f"{len(METRICS)} metrics\n")
    report(result)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(result, indent=2),
                                           encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
