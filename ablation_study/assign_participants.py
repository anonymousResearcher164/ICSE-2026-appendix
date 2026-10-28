"""
assign_participants.py

Allocates the 50 behavior trees in G0/G1/G2 to 12 participants, rebuilding
the task-allocation table after the stratified re-split.

Design the allocation has to satisfy
------------------------------------
  * every participant sees all three guidance conditions, so each gets at
    least one tree from each of G0, G1 and G2 (within-subjects);
  * each participant gets 4 or 5 trees, and all 50 are handed out exactly
    once;
  * no participant gets more than two trees from the same group.

Those constraints fix the shape. Three trees each uses 36, leaving 14 to
distribute as "extras" -- 5 from G0, 5 from G1, 4 from G2 -- so ten
participants receive one extra (4 trees) and two receive two (5 trees).

Why there is a search
---------------------
The constraints say nothing about *which* trees, and the pool ranges from
2 to 186 nodes. A careless draw hands one participant entry_411 (163
nodes) and entry_220 (186) while another gets four small trees, which is a
workload difference large enough to matter for the responses. Draws are
therefore scored on the spread of total nodes per participant and the best
is kept, the same rerandomisation used for the group split.

Run
---
    python3 assign_participants.py            # writes the table
    python3 assign_participants.py --dry-run  # report only
"""

import json
import pathlib
import random
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from characterize_groups import describe

GROUPS = ("G0", "G1", "G2")
PARTICIPANTS = [f"P{i:02d}" for i in range(1, 13)]
MANIFEST = "task_allocation.json"
SEED = 20261028
DRAWS = 30_000


def load_groups() -> dict:
    out = {}
    for g in GROUPS:
        files = sorted(pathlib.Path(g).glob("*.xml"))
        if not files:
            raise SystemExit(f"{g}/ holds no .xml files -- run the split first")
        out[g] = {p.stem: describe(p)["nodes"] for p in files}
    return out


def extras_plan(rng):
    """
    Which participant receives how many extra trees, and from which groups.
    Returns {participant: [group, ...]} or None if the draw conflicts.
    """
    labels = ["G0"] * 5 + ["G1"] * 5 + ["G2"] * 4      # 14 extras
    rng.shuffle(labels)
    doubles = rng.sample(PARTICIPANTS, 2)               # the two who get 5 trees
    slots = []
    for p in PARTICIPANTS:
        slots.extend([p] * (2 if p in doubles else 1))
    plan = {p: [] for p in PARTICIPANTS}
    for participant, label in zip(slots, labels):
        plan[participant].append(label)
    # A participant with two extras must take them from different groups,
    # otherwise they would see three trees from one condition and one from
    # another, which is not the balance the design asks for.
    for p in doubles:
        if len(set(plan[p])) != len(plan[p]):
            return None
    return plan


def draw(groups, rng):
    plan = extras_plan(rng)
    if plan is None:
        return None
    pools = {g: rng.sample(list(groups[g]), len(groups[g])) for g in GROUPS}
    alloc = {p: {g: [] for g in GROUPS} for p in PARTICIPANTS}
    for g in GROUPS:                       # one per participant, then extras
        for p in PARTICIPANTS:
            alloc[p][g].append(pools[g].pop())
    for p, labels in plan.items():
        for g in labels:
            alloc[p][g].append(pools[g].pop())
    if any(pools[g] for g in GROUPS):
        raise SystemExit("internal error: trees left over")
    return alloc


def workload(alloc, groups):
    return {p: sum(groups[g][name] for g in GROUPS for name in alloc[p][g])
            for p in PARTICIPANTS}


def score(alloc, groups):
    loads = list(workload(alloc, groups).values())
    mean = statistics.mean(loads)
    return (max(loads) - min(loads)) / mean if mean else 0.0


def entry_no(name):
    return int(name.split("_")[1])


def render(alloc, groups):
    loads = workload(alloc, groups)
    print(f"  {'Participant':12} {'No guidance (G0)':20} {'Partial (G1)':20} "
          f"{'Full (G2)':20} {'BTs':>4} {'nodes':>6}")
    for p in PARTICIPANTS:
        cells = [", ".join(str(entry_no(n)) for n in sorted(alloc[p][g], key=entry_no))
                 for g in GROUPS]
        n = sum(len(alloc[p][g]) for g in GROUPS)
        print(f"  {p:12} {cells[0]:20} {cells[1]:20} {cells[2]:20} "
              f"{n:4} {loads[p]:6}")
    totals = [sum(len(alloc[p][g]) for p in PARTICIPANTS) for g in GROUPS]
    print(f"  {'total':12} {totals[0]:<20} {totals[1]:<20} {totals[2]:<20} "
          f"{sum(totals):4} {sum(loads.values()):6}")
    print(f"  workload per participant: min {min(loads.values())}, "
          f"max {max(loads.values())}, median "
          f"{statistics.median(loads.values()):.0f} nodes")


def main() -> int:
    groups = load_groups()
    total = sum(len(groups[g]) for g in GROUPS)
    if total != 50:
        raise SystemExit(f"expected 50 trees across the groups, found {total}")

    rng = random.Random(SEED)
    best, best_score = None, float("inf")
    for _ in range(DRAWS):
        candidate = draw(groups, rng)
        if candidate is None:
            continue
        s = score(candidate, groups)
        if s < best_score:
            best, best_score = candidate, s

    # checks that do not trust the construction
    seen = [n for p in PARTICIPANTS for g in GROUPS for n in best[p][g]]
    if len(seen) != 50 or len(set(seen)) != 50:
        raise SystemExit(f"{len(seen)} trees allocated, {len(set(seen))} distinct")
    for p in PARTICIPANTS:
        counts = [len(best[p][g]) for g in GROUPS]
        if min(counts) < 1 or max(counts) > 2 or sum(counts) not in (4, 5):
            raise SystemExit(f"{p} violates the design: {counts}")

    render(best, groups)
    print(f"\n  workload spread: {best_score:.3f}  (0 = every participant equal)")

    if "--dry-run" in sys.argv:
        print("\n--dry-run: no files written")
        return 0

    loads = workload(best, groups)
    with open(MANIFEST, "w") as f:
        json.dump({
            "seed": SEED, "draws": DRAWS,
            "workload_spread": round(best_score, 4),
            "allocation": {p: {g: sorted(best[p][g], key=entry_no) for g in GROUPS}
                           for p in PARTICIPANTS},
            "trees_per_participant": {p: sum(len(best[p][g]) for g in GROUPS)
                                      for p in PARTICIPANTS},
            "nodes_per_participant": loads,
        }, f, indent=2)
    print(f"\nallocation recorded in {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
