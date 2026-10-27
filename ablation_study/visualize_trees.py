"""
visualize_trees.py

Renders every behavior tree in sample_pool/ to a PDF in "study material/",
one PDF per XML file, for use as participant-facing study material.

Rendering choices
-----------------
Layout is top-down via graphviz `dot`, which is what behavior trees want:
a rooted hierarchy with children ordered left to right. Edges are drawn in
document order and `ordering=out` is set, because in a behavior tree the
left-to-right order of children *is* semantics -- a Sequence that ticks B
before A is a different tree, so a layout engine must not reorder siblings
for aesthetics.

Node shapes follow the convention used in the BT literature: control flow
nodes are rectangles carrying their operator symbol, decorators are
diamonds, and leaves are rounded boxes. Leaves are NOT split into action
versus condition shapes: the pool uses custom tags (<EEYThreshold/>,
<SetMGGoal/>) whose kind cannot be told from the XML, and guessing from
the name would put a wrong claim in study material.

Ports are shown in the label because a port value is often the whole
behavioral content of a node -- num_attempts="5" and num_attempts="-1" are
different robots. Literal values are shown plainly; a blackboard
remapping ("{goal}") is shown in grey, since it is data flow rather than a
magnitude. Set SHOW_REMAPPED_PORTS = False to drop the latter on the
larger trees.

Files holding several <BehaviorTree> definitions (subtree libraries -- one
file in this pool has 19) get one PAGE per definition, entry point first,
each page titled with the tree's ID. Laying them out side by side in a
single frame instead produced a page 147 inches wide, which is not
material anyone can read. <TreeNodesModel> is skipped: it declares what
ports a custom node has, and is not part of the behavior.
"""

import html
import os
import re
import pathlib
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import graphviz

SAMPLE_POOL = "sample_pool"
OUTPUT_DIR = "study material"
SHOW_REMAPPED_PORTS = True

# Long literal port values -- pose strings like
# "base_link; 0.7; 0.0; 1.0; 0.0; 0.0; 0.0" -- set the node's box width, and
# a handful of them stretch a whole tree past a readable page. Wrapping them
# at a separator keeps the value fully visible while letting the tree stay
# narrow enough to scale down without the text vanishing. 0 disables.
WRAP_PORT_VALUE_CHARS = 26

# Categorisation drives shape only, so an unlisted tag costs a rounded box
# rather than a wrong rendering. Kept local so this script stays runnable
# on its own, independent of the trace-equivalence checker.
CONTROL_SYMBOLS = {
    "Sequence": "→", "SequenceStar": "→*", "SequenceWithMemory": "→*",
    "ReactiveSequence": "→ᴿ", "PipelineSequence": "→∥",
    "ConditionalSequence": "→?",
    "Fallback": "?", "Selector": "?", "FallbackStar": "?*",
    "ReactiveFallback": "?ᴿ", "RoundRobin": "?↻",
    "Parallel": "⇉", "ParallelNode": "⇉",
    "RecoveryNode": "↺", "IfThenElse": "if", "WhileDoElse": "while",
    "Switch2": "switch", "Switch3": "switch", "Switch4": "switch",
    "Switch5": "switch", "Switch6": "switch", "ManualSelector": "manual",
}
DECORATOR_TAGS = {
    "Inverter", "ForceSuccess", "ForceFailure", "Repeat", "Retry",
    "RepeatUntilSuccess", "RetryUntilSuccessful", "RetryUntilSuccesful",
    "KeepRunningUntilFailure", "Delay", "Timeout", "RateController",
    "DistanceController", "SpeedController", "GoalUpdater", "Loop",
    "Decorator", "ObjectExtractionCondition",
    "BlackboardCheckInt", "BlackboardCheckDouble", "BlackboardCheckString",
}
SKIPPED_TAGS = {"TreeNodesModel", "include"}
IDENTITY_ATTRS = {"ID", "name"}


def esc(s: str) -> str:
    """Escape text for an HTML-like graphviz label."""
    return html.escape(str(s), quote=False)


def wrap_value(value: str) -> str:
    """Break a long port value at its separators, for label width only."""
    if not WRAP_PORT_VALUE_CHARS or len(value) <= WRAP_PORT_VALUE_CHARS:
        return esc(value)
    parts, line, out = re.split(r"(?<=[;,])\s*", value), "", []
    for part in parts:
        if line and len(line) + len(part) > WRAP_PORT_VALUE_CHARS:
            out.append(line)
            line = part
        else:
            line = f"{line} {part}".strip()
    if line:
        out.append(line)
    return "<BR/>".join(esc(p) for p in out)


def node_label(elem: ET.Element) -> str:
    """
    An HTML-like label: tag on the first line, the authored name under it
    when it adds anything, then the ports.
    """
    tag = elem.tag
    lines = []
    symbol = CONTROL_SYMBOLS.get(tag)
    head = f"<B>{esc(symbol)}</B>  {esc(tag)}" if symbol else f"<B>{esc(tag)}</B>"
    lines.append(head)

    authored = elem.attrib.get("name") or elem.attrib.get("ID")
    if authored and authored != tag:
        lines.append(f'<I>{esc(authored)}</I>')

    literal, remapped = [], []
    for attr, value in elem.attrib.items():
        if attr in IDENTITY_ATTRS:
            continue
        v = value.strip()
        if v.startswith("{") and v.endswith("}"):
            remapped.append(f"{esc(attr)}={wrap_value(v)}")
        else:
            literal.append(f"<B>{esc(attr)}={wrap_value(v)}</B>")
    lines.extend(literal)
    if SHOW_REMAPPED_PORTS and remapped:
        lines.append('<FONT COLOR="gray45" POINT-SIZE="9">'
                     + "<BR/>".join(remapped) + "</FONT>")
    return "<" + "<BR/>".join(lines) + ">"


def style_for(elem: ET.Element) -> dict:
    tag = elem.tag
    if tag in CONTROL_SYMBOLS:
        return {"shape": "box", "style": "filled", "fillcolor": "#dce9f7",
                "color": "#4a7fb5"}
    if tag in DECORATOR_TAGS:
        return {"shape": "diamond", "style": "filled", "fillcolor": "#fdf0d5",
                "color": "#c29b4a"}
    if tag == "SubTree" or (not list(elem) and tag.startswith("SubTree")):
        return {"shape": "folder", "style": "filled", "fillcolor": "#e8e0f5",
                "color": "#7b61a8"}
    if not list(elem):
        return {"shape": "box", "style": "rounded,filled", "fillcolor": "#e6f3e6",
                "color": "#5a9e5a"}
    # Has children but is not a tag we categorise: draw it as a container so
    # the structure still reads, rather than silently flattening it.
    return {"shape": "box", "style": "filled,dashed", "fillcolor": "#f2f2f2",
            "color": "#888888"}


def add_subtree(graph, elem: ET.Element, counter: list, parent: str = None) -> None:
    if elem.tag in SKIPPED_TAGS:
        return
    counter[0] += 1
    nid = f"n{counter[0]}"
    graph.node(nid, label=node_label(elem), **style_for(elem))
    if parent is not None:
        graph.edge(parent, nid)
    for child in elem:
        add_subtree(graph, child, counter, nid)


# graphviz's orthogonal edge router (maze.c) aborts on some graphs in this
# pool -- "chkSgraph: Assertion `np->cells[0]' failed". Orthogonal edges read
# best for a tree, so it is tried first and the renderer falls back per page
# rather than giving up on the file or routing every tree the plainer way.
SPLINE_MODES = ("ortho", "polyline", "spline")


def render_graph(graph, out_base: pathlib.Path) -> str:
    """Render `graph`, degrading the edge router until one succeeds."""
    last = None
    for mode in SPLINE_MODES:
        graph.graph_attr["splines"] = mode
        try:
            graph.render(filename=str(out_base), format="pdf", cleanup=True)
            return mode
        except subprocess.CalledProcessError as exc:
            last = exc
            for leftover in (out_base, out_base.with_suffix(".gv")):
                if leftover.exists():
                    leftover.unlink()
    raise last


def build_graph(path: pathlib.Path, title: str, roots, counter: list):
    g = graphviz.Digraph(
        graph_attr={
            "rankdir": "TB",
            # Sibling order is semantics in a BT; do not let dot reorder.
            "ordering": "out",
            "splines": "ortho",
            "nodesep": "0.28",
            "ranksep": "0.45",
            "fontname": "Helvetica",
            "label": title,
            "labelloc": "t",
            "fontsize": "14",
        },
        node_attr={"fontname": "Helvetica", "fontsize": "11", "margin": "0.08,0.05"},
        edge_attr={"arrowsize": "0.7", "color": "#555555"},
    )
    for child in roots:
        add_subtree(g, child, counter)
    return g


def render_file(path: pathlib.Path, out_dir: pathlib.Path, tmp_dir: pathlib.Path) -> tuple:
    text = path.read_text(encoding="utf-8-sig").strip()
    root = ET.fromstring(text)

    if root.tag == "root":
        main_id = root.attrib.get("main_tree_to_execute", "").strip()
        trees = [(bt.attrib.get("ID") or "(unnamed)", bt)
                 for bt in root.findall("BehaviorTree")]
    elif root.tag == "BehaviorTree":
        main_id = root.attrib.get("ID", "").strip()
        trees = [(root.attrib.get("ID") or "(unnamed)", root)]
    else:
        main_id = ""
        trees = [("(fragment)", root)]

    # Drop definitions with nothing drawable, then put the entry point first
    # so page 1 is the tree that actually runs.
    trees = [(tid, bt) for tid, bt in trees
             if [c for c in bt if c.tag not in SKIPPED_TAGS]]
    if not trees:
        raise ValueError("no <BehaviorTree> with drawable children")
    trees.sort(key=lambda t: (t[0] != main_id,))

    counter = [0]
    out_base = out_dir / path.stem
    pages = []
    degraded = []
    for index, (tree_id, bt) in enumerate(trees, start=1):
        kids = [c for c in bt if c.tag not in SKIPPED_TAGS]
        title = path.name
        if len(trees) > 1:
            title += f"   |   {tree_id}"
            title += "  (entry point)" if tree_id == main_id else ""
            title += f"   |   page {index} of {len(trees)}"
        elif main_id:
            title += f"   |   entry point: {main_id}"
        g = build_graph(path, title, kids, counter)
        if len(trees) == 1:
            mode = render_graph(g, out_base)
        else:
            page = tmp_dir / f"{path.stem}_p{index:02d}"
            mode = render_graph(g, page)
            pages.append(f"{page}.pdf")
        if mode != SPLINE_MODES[0]:
            degraded.append((tree_id, mode))

    if pages:
        subprocess.run(["pdfunite", *pages, f"{out_base}.pdf"], check=True)
        for page in pages:
            os.remove(page)
    return len(trees), counter[0], degraded


def main() -> int:
    out_dir = pathlib.Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(pathlib.Path(SAMPLE_POOL).glob("*.xml"))
    if not files:
        raise SystemExit(f"no .xml files in {SAMPLE_POOL}/")

    failures = []
    total_nodes = 0
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="bt_pages_"))
    for path in files:
        try:
            n_trees, n_nodes, degraded = render_file(path, out_dir, tmp_dir)
            total_nodes += n_nodes
            extra = f", {n_trees} trees on {n_trees} pages" if n_trees > 1 else ""
            note = (f"  [edge router fell back to "
                    f"{', '.join(m for _, m in degraded)}]" if degraded else "")
            print(f"  {path.name:20} -> {path.stem}.pdf  ({n_nodes} nodes{extra}){note}")
        except Exception as exc:
            failures.append((path.name, f"{type(exc).__name__}: {exc}"))
            print(f"  {path.name:20} -> FAILED: {type(exc).__name__}: {exc}")

    os.rmdir(tmp_dir) if not os.listdir(tmp_dir) else None
    print(f"\n{len(files) - len(failures)}/{len(files)} rendered to "
          f"{OUTPUT_DIR}/ ({total_nodes} nodes drawn)")
    if failures:
        print("failures:")
        for name, why in failures:
            print(f"  {name}: {why}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
