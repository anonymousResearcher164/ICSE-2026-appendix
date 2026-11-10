"""
assign_groups_stratified.py

Re-partitions sample_pool/ into G0 (17), G1 (17) and G2 (16) so the three
groups are comparable, replacing the plain random split produced by
assign_groups.py.

Why
---
The random split was balanced by file count only, which left the groups
unlike each other in the ways that matter: every tree of 40 nodes or more
landed in G1 or G2, G0's deepest tree nested 6 levels against G1's 18, and
all 25 SubTree references sat outside G2. A difference measured between
conditions would then be partly a difference between the samples.

Method
------
Two stages, both standard and both reportable:

1. Stratification on size. Trees are sorted by node count and cut into
   consecutive strata of three; each stratum contributes one tree to each
   group. This forces the size distributions to track each other -- in
   particular the three largest trees are split one per group instead of
   clustering. The two left over (50 is not divisible by 3) go to G0 and
   G1, which is what makes the groups 17/17/16.

2. Rerandomisation within strata. Which of a stratum's three trees goes to
   which group is still free, so the assignment is drawn many times and
   scored on characteristics stratification does not control -- decorator
   count, SubTree references, literal port values, control-node count --
   and the best-scoring draw is kept. Rerandomisation is a design-stage
   choice made before any outcome is observed, so it does not bias the
   comparison the way post-hoc regrouping would.

Imbalance for one characteristic is (max - min) / mean across the three
groups; the score is a weighted sum, so a draw that is even on everything
scores near zero. Weights are declared in WEIGHTS below.

Run
---
    python3 assign_groups_stratified.py            # writes the split
    python3 assign_groups_stratified.py --dry-run  # report only, no files

Reproducible from SEED. The chosen draw and the before/after comparison
are written to group_assignment_stratified.json.
"""

import collections
import json
import os
import pathlib
import random
import shutil
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from characterize_groups import describe

SAMPLE_POOL = "sample_pool"
GROUPS = ("G0", "G1", "G2")
GROUP_SIZES = {"G0": 17, "G1": 17, "G2": 16}
MANIFEST = "group_assignment_stratified.json"
SEED = 20261028
DRAWS = 40_000

# What the search tries to even out, and how much each matters. Size is
# already handled by the strata, so these are the characteristics that
# stratification leaves free.
WEIGHTS = {
    "total_nodes": 2.0,       # overall reading load per group
    "median_nodes": 2.0,      # the typical tree a participant sees
    "max_depth": 1.0,         # structural difficulty
    "median_depth": 1.0,
    "decorators": 1.5,        # the element type that was most skewed
    "literal_ports": 1.0,     # quantitative detail to report
    "control": 0.5,
    # Counted per TREE, not per occurrence. One tree in the pool holds 19
    # SubTree references, so a raw-count objective can never even them out
    # and would only chase that single outlier. What decides whether a
    # participant meets a construct is how many trees contain it.
    "trees_with_subtree": 1.5,
    "trees_with_decorator": 1.5,
    "subtrees": 0.3,
}


def metrics(d: dict) -> dict:
    kinds = d["kinds"]
    return {
        "name": d["name"],
        "nodes": d["nodes"],
        "depth": d["depth"],
        "control": kinds["control"],
        "decorators": kinds["decorator"],
        "subtrees": kinds["subtree"],
        "literal_ports": d["ports"]["literal"],
    }


def group_totals(members) -> dict:
    sizes = sorted(m["nodes"] for m in members)
    return {
        "trees": len(members),
        "total_nodes": sum(sizes),
        "median_nodes": statistics.median(sizes),
        "max_nodes": sizes[-1],
        "max_depth": max(m["depth"] for m in members),
        "control": sum(m["control"] for m in members),
        "decorators": sum(m["decorators"] for m in members),
        "subtrees": sum(m["subtrees"] for m in members),
        "literal_ports": sum(m["literal_ports"] for m in members),
        "median_depth": statistics.median([m["depth"] for m in members]),
        "trees_with_subtree": sum(1 for m in members if m["subtrees"]),
        "trees_with_decorator": sum(1 for m in members if m["decorators"]),
    }


def imbalance(assignment) -> float:
    totals = {g: group_totals(members) for g, members in assignment.items()}
    score = 0.0
    for key, weight in WEIGHTS.items():
        values = [totals[g][key] for g in GROUPS]
        mean = sum(values) / len(values)
        if mean:
            score += weight * (max(values) - min(values)) / mean
    return score


def draw(strata, rng):
    """One stratified assignment: each full stratum gives one tree per group."""
    assignment = {g: [] for g in GROUPS}
    for stratum in strata:
        if len(stratum) == 3:
            picks = rng.sample(stratum, 3)
            for g, tree in zip(GROUPS, picks):
                assignment[g].append(tree)
        else:
            # The remainder stratum. G2 is the group of 16, so it sits out;
            # the leftovers go to G0 and G1 in random order.
            picks = rng.sample(stratum, len(stratum))
            for g, tree in zip(("G0", "G1"), picks):
                assignment[g].append(tree)
    return assignment


def report(title, assignment):
    print(f"\n{title}")
    print(f"  {'':4} {'n':>3} {'nodes':>6} {'med':>5} {'max':>4} {'dpth':>5} "
          f"{'ctrl':>5} {'dec':>4} {'sub':>4} {'ports':>6} {'trees w/dec':>12} "
          f"{'trees w/sub':>12}")
    for g in GROUPS:
        t = group_totals(assignment[g])
        print(f"  {g:4} {t['trees']:3} {t['total_nodes']:6} "
              f"{t['median_nodes']:5.0f} {t['max_nodes']:4} {t['max_depth']:5} "
              f"{t['control']:5} {t['decorators']:4} {t['subtrees']:4} "
              f"{t['literal_ports']:6} {t['trees_with_decorator']:12} "
              f"{t['trees_with_subtree']:12}")
    print(f"  imbalance score: {imbalance(assignment):.3f}  (0 = identical)")


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    trees = [metrics(describe(p))
             for p in sorted(pathlib.Path(SAMPLE_POOL).glob("*.xml"))]
    if len(trees) != sum(GROUP_SIZES.values()):
        raise SystemExit(f"{len(trees)} trees in {SAMPLE_POOL}/ but group sizes "
                         f"account for {sum(GROUP_SIZES.values())}")
    by_name = {t["name"]: t for t in trees}

    # The split currently on disk, for the before/after comparison.
    previous = None
    if all(pathlib.Path(g).is_dir() for g in GROUPS):
        previous = {g: [by_name[p.stem] for p in sorted(pathlib.Path(g).glob("*.xml"))
                        if p.stem in by_name] for g in GROUPS}
        if not all(previous[g] for g in GROUPS):
            previous = None

    ordered = sorted(trees, key=lambda t: (t["nodes"], t["name"]))
    strata = [ordered[i:i + 3] for i in range(0, len(ordered), 3)]

    rng = random.Random(SEED)
    best, best_score, best_draw = None, float("inf"), -1
    for i in range(DRAWS):
        candidate = draw(strata, rng)
        score = imbalance(candidate)
        if score < best_score:
            best, best_score, best_draw = candidate, score, i
    for g, size in GROUP_SIZES.items():
        if len(best[g]) != size:
            raise SystemExit(f"{g} drew {len(best[g])} trees, expected {size}")

    if previous:
        report("BEFORE  (plain random split)", previous)
    report("AFTER   (stratified + rerandomised)", best)
    print(f"\n  best of {DRAWS:,} draws (draw #{best_draw}, seed {SEED})")

    if dry_run:
        print("\n--dry-run: no files written")
        return 0

    for g in GROUPS:
        pathlib.Path(g).mkdir(exist_ok=True)
        for stale in pathlib.Path(g).glob("*.xml"):
            stale.unlink()
        for tree in best[g]:
            shutil.copy2(os.path.join(SAMPLE_POOL, f"{tree['name']}.xml"),
                         os.path.join(g, f"{tree['name']}.xml"))
        print(f"  {g}: {len(best[g])} trees written")

    assigned = [t["name"] for g in GROUPS for t in best[g]]
    if len(assigned) != len(set(assigned)):
        raise SystemExit("a tree was assigned to more than one group")
    if set(assigned) != set(by_name):
        raise SystemExit("some trees were left unassigned")

    with open(MANIFEST, "w") as f:
        json.dump({
            "method": "size-stratified (strata of 3) + rerandomisation within strata",
            "seed": SEED,
            "draws": DRAWS,
            "chosen_draw": best_draw,
            "weights": WEIGHTS,
            "imbalance_score": round(best_score, 4),
            "previous_imbalance_score": (round(imbalance(previous), 4)
                                         if previous else None),
            "group_sizes": GROUP_SIZES,
            "assignment": {g: sorted(t["name"] for t in best[g]) for g in GROUPS},
            "group_totals": {g: group_totals(best[g]) for g in GROUPS},
        }, f, indent=2)
    print(f"\nassignment recorded in {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
