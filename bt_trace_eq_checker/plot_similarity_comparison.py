"""
plot_similarity_comparison.py

Distribution of per-BT trace similarity for the three description sources.

Why a paired figure rather than three separate box plots
--------------------------------------------------------
All three sources describe the SAME 50 behaviour trees, so the samples are
paired, not independent. Three unpaired boxes would answer "which source
scores higher on average" while hiding the more useful question: is a tree
that one source handles badly also hard for the others? The thin lines
joining each tree's three scores answer that directly, and the boxes still
give the summary.

The scores are the per-tree average order-tolerant trace similarity, read
straight from the checker's reports, so the figure cannot drift from the
numbers in the text.

Usage
-----
    python3 plot_similarity_comparison.py
    python3 plot_similarity_comparison.py --no-lines   # boxes and points only
    python3 plot_similarity_comparison.py --width 7.2  # a two-column float
"""

import argparse
import pathlib
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SOURCES = [
    ("Our method", "report_our_methods.txt", "#4a7fb5"),
    ("Human-authored", "report_human_authored.txt", "#e08a3c"),
    ("BTGenBot", "report_btgenbot.txt", "#5a9e5a"),
]


def read(path: str) -> dict:
    """Per-entry average trace similarity, as a percentage."""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-lines", action="store_true",
                    help="omit the lines joining each tree across sources")
    ap.add_argument("--width", type=float, default=3.4,
                    help="figure width in inches (default 3.4, one column)")
    ap.add_argument("--height", type=float, default=2.6)
    ap.add_argument("--out", default="similarity_comparison")
    args = ap.parse_args()

    data = [(label, read(path), colour) for label, path, colour in SOURCES]
    shared = sorted(set.intersection(*[set(d) for _, d, _ in data]))
    if not shared:
        raise SystemExit("the reports share no entries")
    series = [[d[e] for e in shared] for _, d, _ in data]

    plt.rcParams.update({
        "font.size": 8, "axes.labelsize": 8,
        "xtick.labelsize": 8, "ytick.labelsize": 7.5, "legend.fontsize": 7,
    })
    fig, ax = plt.subplots(figsize=(args.width, args.height))
    positions = np.arange(len(data))
    rng = np.random.default_rng(0)

    # one faint line per behaviour tree, across the three sources
    if not args.no_lines:
        jitter = rng.normal(0, 0.035, size=len(shared))
        for i in range(len(shared)):
            ax.plot(positions + jitter[i], [s[i] for s in series],
                    color="#999999", alpha=0.22, linewidth=0.5, zorder=1)

    bp = ax.boxplot(series, positions=positions, widths=0.5,
                    patch_artist=True, manage_ticks=False, showfliers=False,
                    medianprops=dict(color="#222222", linewidth=1.4))
    for patch, (_, _, colour) in zip(bp["boxes"], data):
        patch.set_facecolor(colour)
        patch.set_alpha(0.35)
        patch.set_edgecolor(colour)
    for part in ("whiskers", "caps"):
        for line, colour in zip(bp[part], [c for _, _, c in data for _ in (0, 1)]):
            line.set_color(colour)

    for pos, values, (_, _, colour) in zip(positions, series, data):
        x = rng.normal(pos, 0.055, size=len(values))
        ax.scatter(x, values, s=7, color=colour, alpha=0.65,
                   linewidths=0, zorder=3)

    ax.set_xticks(positions)
    ax.set_xticklabels([label for label, _, _ in data])
    ax.set_ylim(-4, 104)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Trace similarity (\\%)" if plt.rcParams["text.usetex"]
                  else "Trace similarity (%)")
    ax.yaxis.grid(True, color="#dddddd", linewidth=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout(pad=0.2)

    for ext in ("pdf", "png"):
        fig.savefig(f"{args.out}.{ext}", dpi=300,
                    bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)

    print(f"{len(shared)} behaviour trees, paired across {len(data)} sources")
    for (label, _, _), values in zip(data, series):
        v = sorted(values)
        print(f"  {label:16} median {np.median(v):5.1f}%  mean {np.mean(v):5.1f}%"
              f"  at 100%: {sum(1 for x in v if x == 100):2}"
              f"  below 50%: {sum(1 for x in v if x < 50):2}")
    print(f"written: {args.out}.pdf, {args.out}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
