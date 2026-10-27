"""
render_entry.py

Renders one entry of datasets/reverse_engineered_dataset(594).json to a
single PDF holding both halves of the pair: the behavior tree drawn as a
diagram, and the "mrq" mission requirement text that was reverse-engineered
from it.

The point of the pair is that the two are meant to say the same thing, so
they belong on facing material rather than in two files -- a reader
checking whether the text captures the tree needs both in view.

The diagram is produced by ablation_study/visualize_trees.py, so entries
rendered here use the same visual language as the study material: control
nodes as blue rectangles with their operator symbol, decorators as orange
diamonds, leaves as green rounded boxes, literal port values in bold and
blackboard remappings in grey.

Usage
-----
    python3 render_entry.py            # entry 100, the default
    python3 render_entry.py 42         # any entry number
    python3 render_entry.py 42 out.pdf
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).parent / "ablation_study"))
import visualize_trees as viz

DATASET = "datasets/reverse_engineered_dataset(594).json"

# LaTeX specials that appear in robot-flavoured prose: object_id names carry
# underscores, poses carry percent-free numerics, and namespaces carry
# braces. Left unescaped these are silent corruption, not a compile error.
TEX_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def tex_escape(text: str) -> str:
    return "".join(TEX_ESCAPES.get(c, c) for c in text)


def load_entry(number: int) -> dict:
    with open(DATASET, encoding="utf-8") as f:
        data = json.load(f)
    hits = [r for r in data if str(r.get("entry")).strip() == str(number)]
    if not hits:
        raise SystemExit(f'no record with "entry": {number} in {DATASET}')
    if len(hits) > 1:
        raise SystemExit(f'{len(hits)} records claim "entry": {number}; '
                         f"the dataset is ambiguous for this key")
    return hits[0]


def draw_tree(bt_xml: str, out_base: pathlib.Path) -> tuple:
    root = ET.fromstring(bt_xml.strip())
    if root.tag == "root":
        main_id = root.attrib.get("main_tree_to_execute", "").strip()
        trees = [bt for bt in root.findall("BehaviorTree")]
    elif root.tag == "BehaviorTree":
        main_id = root.attrib.get("ID", "").strip()
        trees = [root]
    else:
        main_id, trees = "", [root]
    if not trees:
        raise SystemExit("the BT field holds no <BehaviorTree> to draw")
    if len(trees) > 1:
        raise SystemExit(f"this entry defines {len(trees)} trees; "
                         f"render it with ablation_study/visualize_trees.py, "
                         f"which paginates them")

    kids = [c for c in trees[0] if c.tag not in viz.SKIPPED_TAGS]
    counter = [0]
    graph = viz.build_graph(out_base, "", kids, counter)
    graph.graph_attr.pop("label", None)          # the PDF supplies its own title
    mode = viz.render_graph(graph, out_base)
    return counter[0], main_id, mode


def page_size(pdf: pathlib.Path) -> tuple:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Page size:"):
            parts = line.split()
            return float(parts[2]), float(parts[4])
    return 0.0, 0.0


def build_pdf(entry: dict, out_pdf: pathlib.Path) -> None:
    number = entry["entry"]
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"entry_{number}_"))
    try:
        diagram = work / "tree"
        nodes, main_id, mode = draw_tree(entry["BT"], diagram)
        width, height = page_size(diagram.with_suffix(".pdf"))

        # A tree much wider than tall is unreadable squeezed onto a portrait
        # page, so the diagram gets a rotated page of its own. The document
        # stays portrait: prose set across a landscape measure runs to ~190
        # characters a line, which is worse to read than the diagram was.
        landscape = width > height * 1.3

        paragraphs = [p.strip() for p in entry["mrq"].split("\n") if p.strip()]
        body = "\n\n".join(tex_escape(p) for p in paragraphs)

        # The page is rotated by choosing landscape geometry for the whole
        # document rather than per-page: pdflscape leaves \textwidth at the
        # portrait measure, which silently shrinks the diagram. The cost is
        # that the prose would then be set across a ~190-character line, so
        # the mrq is confined to a narrower measure of its own below.
        geometry = ("a4paper,landscape,margin=14mm" if landscape
                    else "a4paper,margin=18mm")
        open_ls = close_ls = ""
        # pdflscape rotates the page but leaves \textwidth as the PORTRAIT
        # measure, so on a rotated page the usable width is \textheight and
        # vice versa. Using \textwidth there silently shrinks the diagram to
        # two thirds of the space it could have had.
        graphic = r"width=\textwidth,height=0.78\textheight"
        # Prose is unreadable across a landscape measure; cap the line length.
        measure = r"0.62\textwidth" if landscape else r"\textwidth"
        tex = rf"""\documentclass[11pt]{{article}}
\usepackage[{geometry}]{{geometry}}
\usepackage{{graphicx}}
\usepackage{{parskip}}
\usepackage[T1]{{fontenc}}
\usepackage{{lmodern}}
\usepackage{{microtype}}
\pagestyle{{empty}}
\begin{{document}}

{open_ls}
{{\large\bfseries Behavior tree}}
\vfill
\noindent\makebox[\textwidth]{{%
  \includegraphics[{graphic},keepaspectratio]{{{diagram.name}.pdf}}%
}}
\vfill
{close_ls}
\clearpage
\begin{{center}}
\begin{{minipage}}{{{measure}}}
{{\large\bfseries Mission requirement (mrq)}}

\vspace{{6pt}}
{body}
\end{{minipage}}
\end{{center}}

\end{{document}}
"""
        (work / "doc.tex").write_text(tex, encoding="utf-8")
        for _ in range(2):           # twice, so any layout settles
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "doc.tex"],
                cwd=work, capture_output=True, text=True)
        if not (work / "doc.pdf").exists():
            tail = "\n".join(result.stdout.strip().splitlines()[-25:])
            raise SystemExit(f"pdflatex produced no PDF:\n{tail}")

        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / "doc.pdf", out_pdf)
        print(f"entry {number}: {nodes} nodes, "
              f"{len(paragraphs)} mrq paragraph(s), "
              f"{'landscape' if landscape else 'portrait'} diagram page"
              + (f", edge router fell back to {mode}" if mode != "ortho" else ""))
        print(f"written to {out_pdf}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    num = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    dest = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(f"entry_{num}.pdf")
    build_pdf(load_entry(num), dest)
