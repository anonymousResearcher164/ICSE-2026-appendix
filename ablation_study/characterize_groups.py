"""
characterize_groups.py

Describes the behavior trees in G0/G1/G2 by size and by the kinds of nodes
they contain, and compares the three groups.

Why the comparison matters: the groups were drawn at random, so nothing
guarantees they are alike. If one group happens to collect the deep trees,
or all the trees that use decorators, then a difference measured between
conditions is partly a difference between the samples. This prints the
numbers needed to say whether that happened.

Counting rules
--------------
A "node" is a behavior tree node: <root>, <BehaviorTree> and the
<TreeNodesModel> declaration block are scaffolding and are not counted.
Files defining several <BehaviorTree>s (subtree libraries) count as one
tree whose size is the sum over its definitions, since that is the artifact
a participant is handed.

Depth is the longest root-to-leaf path in nodes, maximised over the
definitions in a file.

Node kinds reuse the categories from visualize_trees.py so the figures
agree with the diagrams: control, decorator, subtree reference, or leaf.
A leaf is structural -- any node without children -- because the pool uses
custom tags whose kind is not recoverable from the XML.
"""

import collections
import pathlib
import statistics
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import visualize_trees as viz

GROUPS = ("G0", "G1", "G2")
SCAFFOLD = {"root", "BehaviorTree"} | viz.SKIPPED_TAGS


def kind_of(elem: ET.Element) -> str:
    if elem.tag in viz.CONTROL_SYMBOLS:
        return "control"
    if elem.tag in viz.DECORATOR_TAGS:
        return "decorator"
    if not list(elem):
        return "subtree" if elem.tag == "SubTree" else "leaf"
    return "uncategorised"      # has children but is not a tag we know


def walk(elem, depth, stats):
    """Accumulate one node and recurse; returns the deepest path seen."""
    if elem.tag in viz.SKIPPED_TAGS:
        return depth - 1
    kind = kind_of(elem)
    stats["kinds"][kind] += 1
    stats["tags"][(kind, elem.tag)] += 1
    for attr, value in elem.attrib.items():
        if attr in ("ID", "name"):
            continue
        v = value.strip()
        stats["ports"]["remapped" if v.startswith("{") and v.endswith("}")
                       else "literal"] += 1
    deepest = depth
    for child in elem:
        deepest = max(deepest, walk(child, depth + 1, stats))
    return deepest


def describe(path: pathlib.Path) -> dict:
    root = ET.fromstring(path.read_text(encoding="utf-8-sig").strip())
    stats = {"kinds": collections.Counter(), "tags": collections.Counter(),
             "ports": collections.Counter()}
    if root.tag == "root":
        definitions = root.findall("BehaviorTree")
    elif root.tag == "BehaviorTree":
        definitions = [root]
    else:
        definitions = []

    depth = 0
    if definitions:
        for bt in definitions:
            for child in bt:
                depth = max(depth, walk(child, 1, stats))
    else:
        depth = walk(root, 1, stats)

    return {
        "name": path.stem,
        "definitions": max(1, len(definitions)),
        "nodes": sum(stats["kinds"].values()),
        "depth": depth,
        "kinds": stats["kinds"],
        "tags": stats["tags"],
        "ports": stats["ports"],
    }


def pct(part, whole):
    return f"{100.0 * part / whole:.0f}%" if whole else "--"


def main() -> int:
    data = {}
    for group in GROUPS:
        files = sorted(pathlib.Path(group).glob("*.xml"))
        if not files:
            raise SystemExit(f"{group}/ holds no .xml files")
        data[group] = [describe(f) for f in files]

    # ---- size -------------------------------------------------------------
    print("SIZE  (nodes per tree; scaffolding excluded)")
    print(f"  {'':5} {'n':>3} {'total':>6} {'min':>4} {'med':>5} {'mean':>6} "
          f"{'max':>4} {'depth med':>10} {'depth max':>10}")
    for group in GROUPS:
        sizes = sorted(d["nodes"] for d in data[group])
        depths = sorted(d["depth"] for d in data[group])
        print(f"  {group:5} {len(sizes):3} {sum(sizes):6} {sizes[0]:4} "
              f"{statistics.median(sizes):5.0f} {statistics.mean(sizes):6.1f} "
              f"{sizes[-1]:4} {statistics.median(depths):10.0f} {depths[-1]:10}")
    allsizes = sorted(d["nodes"] for g in GROUPS for d in data[g])
    print(f"  {'all':5} {len(allsizes):3} {sum(allsizes):6} {allsizes[0]:4} "
          f"{statistics.median(allsizes):5.0f} {statistics.mean(allsizes):6.1f} "
          f"{allsizes[-1]:4}")

    print("\n  largest / smallest per group")
    for group in GROUPS:
        ordered = sorted(data[group], key=lambda d: d["nodes"])
        print(f"    {group}: smallest {ordered[0]['name']} ({ordered[0]['nodes']}), "
              f"largest {ordered[-1]['name']} ({ordered[-1]['nodes']})")

    # ---- composition ------------------------------------------------------
    print("\nCOMPOSITION  (share of nodes by kind)")
    kinds = ("control", "decorator", "leaf", "subtree", "uncategorised")
    print(f"  {'':5} " + " ".join(f"{k:>14}" for k in kinds))
    for group in GROUPS:
        agg = collections.Counter()
        for d in data[group]:
            agg.update(d["kinds"])
        total = sum(agg.values())
        print(f"  {group:5} " + " ".join(
            f"{str(agg[k]) + ' (' + pct(agg[k], total) + ')':>14}" for k in kinds))

    # ---- element types ----------------------------------------------------
    print("\nELEMENT TYPES  (occurrences per group)")
    for wanted in ("control", "decorator", "subtree"):
        rows = collections.defaultdict(lambda: [0, 0, 0])
        for i, group in enumerate(GROUPS):
            for d in data[group]:
                for (kind, tag), n in d["tags"].items():
                    if kind == wanted:
                        rows[tag][i] += n
        if not rows:
            continue
        print(f"\n  {wanted}:")
        print(f"    {'tag':28} {'G0':>5} {'G1':>5} {'G2':>5} {'total':>6}  trees using")
        for tag, counts in sorted(rows.items(), key=lambda kv: -sum(kv[1])):
            using = [sum(1 for d in data[g]
                         if any(t == tag for _, t in d["tags"])) for g in GROUPS]
            print(f"    {tag:28} {counts[0]:5} {counts[1]:5} {counts[2]:5} "
                  f"{sum(counts):6}  {using[0]}/{using[1]}/{using[2]}")

    # distinct leaf vocabulary: how varied the action/condition names are
    print("\n  leaf vocabulary (distinct custom action/condition tags)")
    for group in GROUPS:
        tags = {t for d in data[group] for (k, t) in d["tags"] if k == "leaf"}
        print(f"    {group}: {len(tags)} distinct")
    shared = set.intersection(*[{t for d in data[g] for (k, t) in d["tags"]
                                 if k == "leaf"} for g in GROUPS])
    print(f"    shared by all three: {len(shared)}")

    # ---- ports ------------------------------------------------------------
    print("\nPORTS  (attributes other than ID/name)")
    print(f"  {'':5} {'literal':>9} {'remapped':>9}  trees with >=1 literal")
    for group in GROUPS:
        agg = collections.Counter()
        for d in data[group]:
            agg.update(d["ports"])
        withlit = sum(1 for d in data[group] if d["ports"]["literal"])
        print(f"  {group:5} {agg['literal']:9} {agg['remapped']:9}  "
              f"{withlit}/{len(data[group])}")

    # ---- multi-definition files ------------------------------------------
    print("\nSUBTREE LIBRARIES  (files defining more than one <BehaviorTree>)")
    any_multi = False
    for group in GROUPS:
        multi = [(d["name"], d["definitions"]) for d in data[group]
                 if d["definitions"] > 1]
        if multi:
            any_multi = True
            print(f"  {group}: " + ", ".join(f"{n} ({c} defs)" for n, c in multi))
    if not any_multi:
        print("  none")

    # ---- balance ----------------------------------------------------------
    print("\nBALANCE")
    medians = [statistics.median([d["nodes"] for d in data[g]]) for g in GROUPS]
    totals = [sum(d["nodes"] for d in data[g]) for g in GROUPS]
    print(f"  median size per group: {medians}  (spread "
          f"{max(medians) - min(medians):.0f} nodes)")
    print(f"  total nodes per group: {totals}  (largest/smallest "
          f"{max(totals) / min(totals):.2f}x)")
    for wanted in ("control", "decorator"):
        concentrated = []
        rows = collections.defaultdict(lambda: [0, 0, 0])
        for i, group in enumerate(GROUPS):
            for d in data[group]:
                for (kind, tag), n in d["tags"].items():
                    if kind == wanted:
                        rows[tag][i] += n
        for tag, counts in rows.items():
            if sum(counts) >= 4 and sum(1 for c in counts if c == 0) >= 2:
                concentrated.append((tag, counts))
        if concentrated:
            print(f"  {wanted} types confined to one group "
                  f"(>=4 occurrences, absent from two groups):")
            for tag, counts in sorted(concentrated, key=lambda kv: -sum(kv[1])):
                print(f"    {tag}: {counts}")
        else:
            print(f"  no {wanted} type of any frequency sits in just one group")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
