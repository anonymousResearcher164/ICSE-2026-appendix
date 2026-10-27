"""
assign_groups.py

Randomly partitions the 50 behavior trees in sample_pool/ into the three
ablation groups: 17 to G0, 17 to G1, 16 to G2.

The split is seeded so it can be reproduced and reported. Two details make
that reproducibility real rather than nominal:

  - the pool is sorted before shuffling, because os.listdir() returns
    entries in filesystem order, which differs between machines and even
    between runs after files are rewritten. Shuffling an unsorted list
    with a fixed seed still gives a different split elsewhere.
  - the assignment is written to group_assignment.json, so the exact
    partition behind a reported result stays on record even if the script
    is later re-run with a different seed.

Trees are COPIED, not moved: sample_pool/ stays intact as the record of
what was drawn from, and the script can be re-run. Set MOVE = True to
move them instead.

Re-running refuses to touch groups that already hold trees, so a second
run cannot quietly mix a new split into an old one. Set FORCE = True to
clear and re-assign.
"""

import json
import os
import random
import shutil

SEED = 42
SAMPLE_POOL = "sample_pool"
GROUP_SIZES = {"G0": 17, "G1": 17, "G2": 16}
MANIFEST = "group_assignment.json"
MOVE = False
FORCE = False


# --- collect the pool ------------------------------------------------------

# sorted() is load-bearing for reproducibility, not cosmetic -- see docstring.
pool = sorted(f for f in os.listdir(SAMPLE_POOL) if f.endswith(".xml"))

expected = sum(GROUP_SIZES.values())
if len(pool) != expected:
    raise SystemExit(
        f"{SAMPLE_POOL}/ holds {len(pool)} .xml files but the group sizes "
        f"{GROUP_SIZES} account for {expected}. Fix the pool or the sizes "
        f"rather than letting trees go unassigned."
    )

# --- refuse to overwrite an existing split ---------------------------------

occupied = {}
for group in GROUP_SIZES:
    if os.path.isdir(group):
        present = [f for f in os.listdir(group) if f.endswith(".xml")]
        if present:
            occupied[group] = len(present)
if occupied and not FORCE:
    raise SystemExit(
        f"These groups already hold trees: {occupied}. Re-running would mix "
        f"a new split into the old one. Set FORCE = True to clear and "
        f"re-assign, or move the existing split aside first."
    )

# --- shuffle and slice -----------------------------------------------------

shuffled = list(pool)
random.Random(SEED).shuffle(shuffled)

assignment = {}
start = 0
for group, size in GROUP_SIZES.items():
    assignment[group] = sorted(shuffled[start:start + size])
    start += size

# --- write the files -------------------------------------------------------

for group, files in assignment.items():
    os.makedirs(group, exist_ok=True)
    if FORCE:
        for stale in os.listdir(group):
            if stale.endswith(".xml"):
                os.remove(os.path.join(group, stale))
    for name in files:
        src = os.path.join(SAMPLE_POOL, name)
        dst = os.path.join(group, name)
        shutil.move(src, dst) if MOVE else shutil.copy2(src, dst)
    print(f"{group}: {len(files)} trees")

# --- record the split ------------------------------------------------------

with open(MANIFEST, "w") as f:
    json.dump({
        "seed": SEED,
        "sample_pool": SAMPLE_POOL,
        "pool_size": len(pool),
        "mode": "move" if MOVE else "copy",
        "group_sizes": GROUP_SIZES,
        "assignment": assignment,
    }, f, indent=2)

# --- verify ----------------------------------------------------------------

assigned = [name for files in assignment.values() for name in files]
if len(assigned) != len(set(assigned)):
    raise SystemExit("a tree was assigned to more than one group")
if set(assigned) != set(pool):
    missing = set(pool) - set(assigned)
    raise SystemExit(f"trees left unassigned: {sorted(missing)}")
for group, size in GROUP_SIZES.items():
    on_disk = len([f for f in os.listdir(group) if f.endswith(".xml")])
    if on_disk != size:
        raise SystemExit(f"{group} holds {on_disk} trees, expected {size}")

print(f"\n{len(assigned)} trees partitioned across {len(GROUP_SIZES)} groups, "
      f"no overlap, none left over (seed {SEED})")
print(f"assignment recorded in {MANIFEST}")
