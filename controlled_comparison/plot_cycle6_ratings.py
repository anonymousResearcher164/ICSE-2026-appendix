"""
plot_cycle6_ratings.py

Box plots of the Cycle 6 quality ratings -- Atomicity, Ambiguity,
Completeness, Understandability and Consistency -- for the Prompt Engineer
and the two Independent Evaluators.

Data
----
Read straight from the "Cycle 6" tab of the ratings spreadsheet via its CSV
export, so the figure tracks the sheet rather than a stale copy. Pass --csv
to plot a saved export instead (offline, or to freeze the figure for a
paper). The sheet lays each rater out as a seven-column block -- average,
the five metrics, a comment -- which is what COLUMN_BLOCKS encodes.

A note on reading these plots
-----------------------------
The ratings are ordinal on a 1-4 scale and strongly ceiling-weighted: 526
of the 750 values are 4. Every rater/metric combination except the Prompt
Engineer's Completeness has a median of 4, so the median line and the
upper hinge sit on top of each other and the box degenerates to a line for
most groups, with the low ratings rendered as outlier dots.

That is a property of the data, not a plotting fault, but it does mean the
boxes alone understate what is going on. Each box therefore carries the
individual ratings jittered on top of it, so the reader can see how many
3s and 2s sit behind a flat-looking box. For the same reason the printed
summary reports the full rating distribution, which is the honest summary
for ordinal data of this shape.

Usage
-----
    python3 plot_cycle6_ratings.py
    python3 plot_cycle6_ratings.py --csv cycle6.csv --out figures/
    python3 plot_cycle6_ratings.py --stats ac2
    python3 plot_cycle6_ratings.py --stats ac2,disagree
"""

import argparse
import csv
import io
import pathlib
import statistics
import sys
import urllib.request

import matplotlib
matplotlib.use("Agg")
try:
    # One implementation of the coefficients, already covered by
    # rater_agreement.py --selftest. If that file is missing the figure
    # still draws, just without the agreement row.
    from rater_agreement import krippendorff_alpha, gwet_ac2, CATEGORIES
    _STATS = True
except ImportError:                                   # pragma: no cover
    _STATS = False
import matplotlib.pyplot as plt
import numpy as np

SHEET_ID = "1dFsFHVwUJwyIzfKWSGMjw5ZhkZF0lp-cDpTPyc932eo"
GID = "715791249"                      # the "Cycle 6" tab
CSV_URL = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
           f"/export?format=csv&gid={GID}")

METRICS = ["Atomicity", "Ambiguity", "Completeness",
           "Understandability", "Consistency"]
# first column of each rater's block; the five metrics follow the average
COLUMN_BLOCKS = {"Prompt Engineer": 2,
                 "Independent Evaluator 1": 9,
                 "Independent Evaluator 2": 16}
SCALE = (1, 4)
COLOURS = {"Prompt Engineer": "#4a7fb5",
           "Independent Evaluator 1": "#e08a3c",
           "Independent Evaluator 2": "#5a9e5a"}


def load(csv_path=None) -> list:
    if csv_path:
        text = pathlib.Path(csv_path).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(CSV_URL, timeout=60) as response:
            text = response.read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 4:
        raise SystemExit("the export has no data rows -- is the tab shared?")
    header = rows[2]
    # Fail loudly if the sheet is rearranged, rather than plotting the
    # wrong columns under the right labels.
    for rater, base in COLUMN_BLOCKS.items():
        found = header[base + 1:base + 1 + len(METRICS)]
        if found != METRICS:
            raise SystemExit(
                f"columns for {rater} are {found}, expected {METRICS}. "
                f"The sheet layout has changed; update COLUMN_BLOCKS.")
    return [r for r in rows[3:] if r and r[0].strip()]


def extract(rows) -> dict:
    out = {}
    for rater, base in COLUMN_BLOCKS.items():
        for i, metric in enumerate(METRICS):
            values = []
            for row in rows:
                cell = row[base + 1 + i].strip() if len(row) > base + 1 + i else ""
                if cell:
                    try:
                        values.append(float(cell))
                    except ValueError:
                        print(f"  skipped non-numeric {cell!r} "
                              f"({rater}/{metric}, {row[0]})", file=sys.stderr)
            out[(rater, metric)] = values
    return out


# What each key prints under an attribute, and how to format it. Keeping
# agreement and disagreement in one place means a figure can carry either
# or both without the two being computed in different ways.
STAT_KEYS = ("alpha", "ac2", "unanimous", "disagree", "contested")
STAT_LABEL = {"alpha": "$\\alpha$", "ac2": "AC2", "unanimous": "unanim.",
              "disagree": "disagr.", "contested": "contested"}


def coefficients(rows) -> dict:
    """
    Per metric: the two agreement coefficients, plus the three-rater
    disagreement figures. "disagree" is the complement of raw agreement --
    the share of descriptions where at least one rater chose a different
    level -- and "contested" counts only those spanning two levels or more.
    """
    out = {}
    for i, metric in enumerate(METRICS):
        units = []
        for row in rows:
            vals = []
            for base in COLUMN_BLOCKS.values():
                cell = row[base + 1 + i].strip() if len(row) > base + 1 + i else ""
                if cell:
                    vals.append(float(cell))
            if len(vals) >= 2:
                units.append(vals)
        ranges = [max(u) - min(u) for u in units]
        n = len(ranges) or 1
        out[metric] = {
            "alpha": krippendorff_alpha(units, CATEGORIES),
            "ac2": gwet_ac2(units, CATEGORIES, "quadratic"),
            "unanimous": sum(1 for r in ranges if r == 0) / n,
            "disagree": sum(1 for r in ranges if r > 0) / n,
            "contested": sum(1 for r in ranges if r >= 2) / n,
        }
    return out


def format_stat(key, value):
    if value != value:
        return f"{STAT_LABEL[key]} n/a"
    if key in ("alpha", "ac2"):
        return f"{STAT_LABEL[key]} {value:.2f}"
    return f"{STAT_LABEL[key]} {value:.0%}"


def plot(data, out_dir: pathlib.Path, n_items: int, height: float = 3.3,
         stats: dict = None, width: float = 7.2, which=("ac2",)):
    raters = list(COLUMN_BLOCKS)
    box_width = 0.24
    # Only four rating levels ever appear, so the vertical axis carries very
    # little information per inch. A short, wide canvas shows the same data
    # without the empty bands a taller figure leaves between the levels.
    fig, ax = plt.subplots(figsize=(width, height))
    rng = np.random.default_rng(0)      # jitter, fixed so the figure is stable

    for r, rater in enumerate(raters):
        offset = (r - (len(raters) - 1) / 2) * (box_width + 0.04)
        positions = np.arange(len(METRICS)) + offset
        series = [data[(rater, m)] for m in METRICS]
        bp = ax.boxplot(series, positions=positions, widths=box_width,
                        patch_artist=True, manage_ticks=False,
                        medianprops=dict(color="#222222", linewidth=1.6),
                        flierprops=dict(marker="", markersize=0))
        for patch in bp["boxes"]:
            patch.set_facecolor(COLOURS[rater])
            patch.set_alpha(0.45)
            patch.set_edgecolor(COLOURS[rater])
        for part in ("whiskers", "caps"):
            for line in bp[part]:
                line.set_color(COLOURS[rater])
        # the individual ratings, so a flat box still shows its spread
        for pos, values in zip(positions, series):
            x = rng.normal(pos, box_width * 0.16, size=len(values))
            ax.scatter(x, values, s=7, color=COLOURS[rater],
                       alpha=0.5, linewidths=0, zorder=3)
        ax.scatter([], [], s=40, color=COLOURS[rater], label=rater)

    ax.set_xticks(np.arange(len(METRICS)))
    if stats:
        # Two lines, not one. The figure is drawn at roughly the width it
        # will occupy on the page, so LaTeX barely rescales it and the point
        # sizes here are the point sizes a reader gets. Side-by-side labels
        # are ~35 characters and simply do not fit that width at a readable
        # size; split over two lines each half is comfortable at 8-9pt.
        labels = []
        for metric in METRICS:
            parts = [format_stat(k, stats[metric][k]) for k in which]
            labels.append(f"{metric}\n" + "   ".join(parts))
        size = 9.0
        while size >= 6.0:
            ax.set_xticklabels(labels, fontsize=size)
            fig.canvas.draw()
            boxes = [t.get_window_extent() for t in ax.get_xticklabels()]
            if all(boxes[i].x1 + 3 < boxes[i + 1].x0
                   for i in range(len(boxes) - 1)):
                print(f"  x labels fitted at {size:.1f}pt "
                      f"(figure {width:.1f} x {height:.1f} in)")
                break
            size -= 0.5
        else:
            print(f"  note: x labels still crowded at {size:.1f}pt",
                  file=sys.stderr)
    else:
        ax.set_xticklabels(METRICS)
    ax.set_ylim(SCALE[0] - 0.35, SCALE[1] + 0.35)
    ax.set_yticks(range(SCALE[0], SCALE[1] + 1))
    ax.set_ylabel("Rating")
    #ax.set_title(f"Cycle 6 quality ratings by metric and rater "f"(n = {n_items} behavior trees)")
    ax.yaxis.grid(True, color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    # Key above the plot: with the title off, the strip over the axes is
    # otherwise empty, so the legend costs no height there, where below the
    # axis it had to clear the tick labels and the x-axis label.
    ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              ncol=3, fontsize=9.5, handletextpad=0.4, columnspacing=1.6,
              borderaxespad=0.2)
    ax.tick_params(axis="y", labelsize=9.5)
    ax.set_ylabel("Rating", fontsize=10)
    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for ext in ("png", "pdf"):
        path = out_dir / f"cycle6_ratings_boxplot.{ext}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        written.append(path)
    plt.close(fig)
    return written


def summarise(data):
    print(f"  {'metric':18} {'rater':24} {'n':>3} {'med':>4} {'IQR':>9} "
          f"{'min':>4} {'  distribution 1/2/3/4'}")
    for metric in METRICS:
        for rater in COLUMN_BLOCKS:
            v = data[(rater, metric)]
            q1, q3 = np.percentile(v, [25, 75])
            dist = "/".join(str(sum(1 for x in v if x == k))
                            for k in range(SCALE[0], SCALE[1] + 1))
            print(f"  {metric:18} {rater:24} {len(v):3} "
                  f"{statistics.median(v):4.1f} {q1:4.1f}-{q3:<4.1f} "
                  f"{min(v):4.0f}   {dist}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    parser.add_argument("--csv", help="read a saved CSV export instead of the sheet")
    parser.add_argument("--out", default=".", help="directory for the figures")
    parser.add_argument("--stats", default="alpha,ac2",
                        help="comma-separated list of what to print under each "
                             "attribute, from: " + ", ".join(STAT_KEYS) +
                             ". 'none' prints nothing. Examples: --stats ac2 | "
                             "--stats ac2,disagree (default: alpha,ac2)")
    parser.add_argument("--no-stats", action="store_true",
                        help=argparse.SUPPRESS)        # kept working; use --stats none
    parser.add_argument("--height", type=float, default=3.3,
                        help="figure height in inches (default 3.3)")
    parser.add_argument("--width", type=float, default=7.2,
                        help="figure width in inches (default 7.2, about a two-column "
                             "text width so LaTeX need not rescale); wider "
                             "lets the x labels keep a larger font")
    args = parser.parse_args()

    rows = load(args.csv)
    data = extract(rows)
    print(f"{len(rows)} behavior trees x {len(COLUMN_BLOCKS)} raters "
          f"x {len(METRICS)} metrics\n")
    summarise(data)
    if args.no_stats:                 # older flag, same effect as --stats none
        args.stats = "none"
    wanted = [] if args.stats.strip().lower() == "none" else [
        k.strip().lower() for k in args.stats.split(",") if k.strip()]
    if args.stats.strip().lower() == "both":          # older spelling
        wanted = ["alpha", "ac2"]
    unknown = [k for k in wanted if k not in STAT_KEYS]
    if unknown:
        raise SystemExit(f"unknown --stats key(s) {unknown}; "
                         f"choose from {list(STAT_KEYS)} or 'none'")
    stats = None
    if _STATS and wanted:
        stats = coefficients(rows)
        print(f"\n  per metric (figure shows: {', '.join(wanted)}):")
        for metric, v in stats.items():
            print(f"    {metric:18} alpha {v['alpha']:+.3f}  AC2 {v['ac2']:+.3f}"
                  f"  unanimous {v['unanimous']:4.0%}"
                  f"  disagree {v['disagree']:4.0%}"
                  f"  contested {v['contested']:4.0%}")
    written = plot(data, pathlib.Path(args.out), len(rows), args.height,
                   stats, args.width, wanted)
    print("\nwritten: " + ", ".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
