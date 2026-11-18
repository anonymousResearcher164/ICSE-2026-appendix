"""
main.py

Compares pairs of behavior-tree XML *files* for execution-trace equivalence
and writes a full report to an output file.

Usage
-----
Run as-is to execute the three built-in example cases (edit CASES below to
point at your own files), or call run_case(...) directly with your own
file paths, e.g.:

    run_case("reference vs candidate", "trees/ref.xml", "trees/candidate.xml")

Each call appends a section to the report file (default: report.txt) and
also prints a short verdict line to the console so you get immediate
feedback without having to open the file.
"""

from pathlib import Path

from bt_trace_equivalence import BTEquivalenceChecker

# Where the full report gets written. Pass a different path to run_case()
# calls below if you want per-case files instead of one combined report.
DEFAULT_OUTPUT_PATH = "report.txt"


def read_xml(path: str) -> str:
    """
    Read a BT XML file from disk, raising a clear error if it's missing.

    Tolerates common real-world quirks that otherwise trip up
    xml.etree.ElementTree's strict parser:
      - UTF-8 byte-order mark (BOM), common in files saved by some Windows
        tools / editors. encoding='utf-8-sig' strips it if present.
      - Leading/trailing blank lines or whitespace before the <?xml ... ?>
        declaration, which ElementTree rejects with:
        "XML or text declaration not at start of entity".
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Behavior tree XML file not found: {p.resolve()}")
    text = p.read_text(encoding="utf-8-sig")
    return text.strip()


def run_case(title: str, path_a: str, path_b: str,
             output_path: str = DEFAULT_OUTPUT_PATH, mode: str = "auto",
             max_leaves: int = 14, ignore_name_style: bool = False):
    """
    Load two BT XML files by path, check trace equivalence, append the full
    report to `output_path`, and print a one-line verdict to the console.

    If either file fails to load or parse, the error is logged to the report
    (with a [PARSE ERROR] verdict) and to the console, and None is returned
    instead of raising -- so one malformed file in a batch doesn't abort the
    rest of the run.
    """
    try:
        xml_a = read_xml(path_a)
        xml_b = read_xml(path_b)
        checker = BTEquivalenceChecker()
        report = checker.check(xml_a, xml_b, mode=mode,
                               max_exhaustive_leaves=max_leaves,
                               ignore_name_style=ignore_name_style)
    except Exception as exc:  # noqa: BLE001 -- intentionally broad: keep the batch alive
        section_text = (
            "=" * 78 + "\n" +
            f"{title}\n" +
            f"  Tree A: {path_a}\n" +
            f"  Tree B: {path_b}\n" +
            "=" * 78 + "\n" +
            f"Result: PARSE ERROR\n" +
            f"  {type(exc).__name__}: {exc}\n\n"
        )
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(section_text)
        print(f"[PARSE ERROR] {title}  (A={path_a}, B={path_b})  -> {type(exc).__name__}: {exc}")
        return None

    section_lines = [
        "=" * 78,
        title,
        f"  Tree A: {path_a}",
        f"  Tree B: {path_b}",
        "=" * 78,
        report.summary(),
        "",
    ]
    section_text = "\n".join(section_lines)

    # Append so multiple run_case() calls build up one combined report file.
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(section_text)

    verdict = "EQUIVALENT" if report.equivalent else "NOT EQUIVALENT"
    print(f"[{verdict}] {title}  (A={path_a}, B={path_b})  -> see {output_path}")

    return report


def write_summary(results, output_path: str) -> None:
    """
    Put an overall summary at the top of the report, and print it.

    The verdicts are broken down by how they were reached. An "equivalent"
    established by enumerating every scenario is a proof over the modelled
    space; one established by sampling means only that no counterexample
    turned up in the scenarios drawn. The two are not the same claim, and a
    reader of the report should not have to open each section to tell them
    apart.
    """
    decided = [(name, r) for name, r in results if r is not None]
    failed = [name for name, r in results if r is None]

    def kind(report):
        return "exhaustive" if report.mode.startswith("exhaustive") else "sampled"

    rows = {}
    for name, report in decided:
        verdict = "equivalent" if report.equivalent else "not equivalent"
        rows.setdefault((verdict, kind(report)), []).append(name)

    lines = ["=" * 78, "SUMMARY", "=" * 78,
             f"Cases: {len(results)}   decided: {len(decided)}   "
             f"not decided: {len(failed)}", ""]
    lines.append(f"  {'verdict':16} {'exhaustive':>11} {'sampled':>9} {'total':>7}")
    for verdict in ("equivalent", "not equivalent"):
        ex = len(rows.get((verdict, "exhaustive"), []))
        sa = len(rows.get((verdict, "sampled"), []))
        lines.append(f"  {verdict:16} {ex:11} {sa:9} {ex + sa:7}")
    ex_all = sum(1 for _, r in decided if kind(r) == "exhaustive")
    lines.append(f"  {'total':16} {ex_all:11} {len(decided) - ex_all:9} "
                 f"{len(decided):7}")

    sampled = [(name, r) for name, r in decided if kind(r) == "sampled"]
    if sampled:
        lines += ["",
                  "  Decided by sampling, so 'equivalent' means no "
                  "counterexample was found,",
                  "  not that none exists:"]
        for name, report in sampled:
            lines.append(f"    {name}: {report.scenarios_checked:,} scenarios "
                         f"of a space too large to enumerate")
    if failed:
        lines += ["", "  Not decided (see the sections above):"]
        lines += [f"    {name}" for name in failed]
    lines.append("")

    # Prepended, not appended: the summary is what a reader wants first, and
    # the per-case sections run to hundreds of lines. It can only be written
    # once every case has run, so the file is rebuilt rather than streamed.
    text = "\n".join(lines)
    sections = Path(output_path).read_text(encoding="utf-8")
    Path(output_path).write_text(text + "\n" + sections, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Check each ground-truth tree against its counterpart.")
    parser.add_argument("--mode", choices=("auto", "exhaustive", "sample"),
                        default="auto",
                        help="'exhaustive' enumerates all 2**N leaf-outcome "
                             "scenarios and refuses above max-leaves; 'sample' "
                             "always samples; 'auto' enumerates when the space "
                             "is small enough and samples when it is not "
                             "(default: auto)")
    parser.add_argument("--max-leaves", type=int, default=14,
                        help="largest leaf count still enumerated exhaustively "
                             "(default: 14)")
    parser.add_argument("--ignore-name-style", action="store_true",
                        help="treat node names that differ only in case or in "
                             "underscores/hyphens as the same name, so "
                             "check_battery matches CheckBattery. Names that "
                             "differ by a word are still different.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH,
                        help=f"report file (default: {DEFAULT_OUTPUT_PATH})")
    args = parser.parse_args()

    # Start each run with a fresh report file rather than appending forever.
    Path(args.output).write_text("", encoding="utf-8")

    # Edit these to point at your own BT XML files.
    # CASES = [
    #     ("Entry 6: ",
    #      "gt/entry_6.xml", "om/entry_6.xml"),
    #     ("Entry 32: ",
    #      "gt/entry_32.xml", "om/entry_32.xml"),
    #     ("Entry 41: ",
    #      "gt/entry_41.xml", "om/entry_41.xml"),
    #     ("Entry 50: ",
    #      "gt/entry_50.xml", "om/entry_50.xml"),
    #     ("Entry 58: ",
    #      "gt/entry_58.xml", "om/entry_58.xml"),
    #     ("Entry 64: ",
    #      "gt/entry_64.xml", "om/entry_64.xml"),
    #     ("Entry 73: ",
    #      "gt/entry_73.xml", "om/entry_73.xml"),
    #     ("Entry 77: ",
    #      "gt/entry_77.xml", "om/entry_77.xml"),
    #     ("Entry 89: ",
    #      "gt/entry_89.xml", "om/entry_89.xml"),
    #     ("Entry 92: ",
    #      "gt/entry_92.xml", "om/entry_92.xml"),
    #     ("Entry 108: ",
    #      "gt/entry_108.xml", "om/entry_108.xml"),
    #     ("Entry 109: ",
    #      "gt/entry_109.xml", "om/entry_109.xml"),
    #     ("Entry 124: ",
    #      "gt/entry_124.xml", "om/entry_124.xml"),
    #     ("Entry 150: ",
    #      "gt/entry_150.xml", "om/entry_150.xml"),
    #     ("Entry 155: ",
    #      "gt/entry_155.xml", "om/entry_155.xml"),
    #     ("Entry 194: ",
    #      "gt/entry_194.xml", "om/entry_194.xml"),
    #     ("Entry 220: ",
    #      "gt/entry_220.xml", "om/entry_220.xml"),
    #     ("Entry 248: ",
    #      "gt/entry_248.xml", "om/entry_248.xml"),
    #     ("Entry 256: ",
    #      "gt/entry_256.xml", "om/entry_256.xml"),
    #     ("Entry 266: ",
    #      "gt/entry_266.xml", "om/entry_266.xml"),
    #     ("Entry 272: ",
    #      "gt/entry_272.xml", "om/entry_272.xml"),
    #     ("Entry 317: ",
    #      "gt/entry_317.xml", "om/entry_317.xml"),
    #     ("Entry 321: ",
    #      "gt/entry_321.xml", "om/entry_321.xml"),
    #     ("Entry 325: ",
    #      "gt/entry_325.xml", "om/entry_325.xml"),
    #     ("Entry 345: ",
    #      "gt/entry_345.xml", "om/entry_345.xml"),
    #     ("Entry 371: ",
    #      "gt/entry_371.xml", "om/entry_371.xml"),
    #     ("Entry 375: ",
    #      "gt/entry_375.xml", "om/entry_375.xml"),
    #     ("Entry 376: ",
    #      "gt/entry_376.xml", "om/entry_376.xml"),
    #     ("Entry 380: ",
    #      "gt/entry_380.xml", "om/entry_380.xml"),
    #     ("Entry 402: ",
    #      "gt/entry_402.xml", "om/entry_402.xml"),
    #     ("Entry 411: ",
    #      "gt/entry_411.xml", "om/entry_411.xml"),
    #     ("Entry 418: ",
    #      "gt/entry_418.xml", "om/entry_418.xml"),
    #     ("Entry 432: ",
    #      "gt/entry_432.xml", "om/entry_432.xml"),
    #     ("Entry 435: ",
    #      "gt/entry_435.xml", "om/entry_435.xml"),
    #     ("Entry 440: ",
    #      "gt/entry_440.xml", "om/entry_440.xml"),
    #     ("Entry 451: ",
    #      "gt/entry_451.xml", "om/entry_451.xml"),
    #     ("Entry 457: ",
    #      "gt/entry_457.xml", "om/entry_457.xml"),
    #     ("Entry 467: ",
    #      "gt/entry_467.xml", "om/entry_467.xml"),
    #     ("Entry 475: ",
    #      "gt/entry_475.xml", "om/entry_475.xml"),
    #     ("Entry 485: ",
    #      "gt/entry_485.xml", "om/entry_485.xml"),
    #     ("Entry 493: ",
    #      "gt/entry_493.xml", "om/entry_493.xml"),
    #     ("Entry 511: ",
    #      "gt/entry_511.xml", "om/entry_511.xml"),
    #     ("Entry 515: ",
    #      "gt/entry_515.xml", "om/entry_515.xml"),
    #     ("Entry 529: ",
    #      "gt/entry_529.xml", "om/entry_529.xml"),
    #     ("Entry 531: ",
    #      "gt/entry_531.xml", "om/entry_531.xml"),
    #     ("Entry 540: ",
    #      "gt/entry_540.xml", "om/entry_540.xml"),
    #     ("Entry 549: ",
    #      "gt/entry_549.xml", "om/entry_549.xml"),
    #     ("Entry 552: ",
    #      "gt/entry_552.xml", "om/entry_552.xml"),
    #     ("Entry 566: ",
    #      "gt/entry_566.xml", "om/entry_566.xml"),
    #     ("Entry 584: ",
    #      "gt/entry_584.xml", "om/entry_584.xml"),
    # ]

    CASES = [
        ("Entry 6: ", "gt/entry_6.xml", "ha/entry_6.xml"),
        ("Entry 32: ", "gt/entry_32.xml", "ha/entry_32.xml"),
        ("Entry 41: ", "gt/entry_41.xml", "ha/entry_41.xml"),
        ("Entry 50: ", "gt/entry_50.xml", "ha/entry_50.xml"),
        ("Entry 58: ", "gt/entry_58.xml", "ha/entry_58.xml"),
        ("Entry 64: ", "gt/entry_64.xml", "ha/entry_64.xml"),
        ("Entry 73: ", "gt/entry_73.xml", "ha/entry_73.xml"),
        ("Entry 77: ", "gt/entry_77.xml", "ha/entry_77.xml"),
        ("Entry 89: ", "gt/entry_89.xml", "ha/entry_89.xml"),
        ("Entry 92: ", "gt/entry_92.xml", "ha/entry_92.xml"),
        ("Entry 108: ", "gt/entry_108.xml", "ha/entry_108.xml"),
        ("Entry 109: ", "gt/entry_109.xml", "ha/entry_109.xml"),
        ("Entry 124: ", "gt/entry_124.xml", "ha/entry_124.xml"),
        ("Entry 150: ", "gt/entry_150.xml", "ha/entry_150.xml"),
        ("Entry 155: ", "gt/entry_155.xml", "ha/entry_155.xml"),
        ("Entry 194: ", "gt/entry_194.xml", "ha/entry_194.xml"),
        ("Entry 220: ", "gt/entry_220.xml", "ha/entry_220.xml"),
        ("Entry 248: ", "gt/entry_248.xml", "ha/entry_248.xml"),
        ("Entry 256: ", "gt/entry_256.xml", "ha/entry_256.xml"),
        ("Entry 266: ", "gt/entry_266.xml", "ha/entry_266.xml"),
        ("Entry 272: ", "gt/entry_272.xml", "ha/entry_272.xml"),
        ("Entry 317: ", "gt/entry_317.xml", "ha/entry_317.xml"),
        ("Entry 321: ", "gt/entry_321.xml", "ha/entry_321.xml"),
        ("Entry 325: ", "gt/entry_325.xml", "ha/entry_325.xml"),
        ("Entry 345: ", "gt/entry_345.xml", "ha/entry_345.xml"),
        ("Entry 371: ", "gt/entry_371.xml", "ha/entry_371.xml"),
        ("Entry 375: ", "gt/entry_375.xml", "ha/entry_375.xml"),
        ("Entry 376: ", "gt/entry_376.xml", "ha/entry_376.xml"),
        ("Entry 380: ", "gt/entry_380.xml", "ha/entry_380.xml"),
        ("Entry 402: ", "gt/entry_402.xml", "ha/entry_402.xml"),
        ("Entry 411: ", "gt/entry_411.xml", "ha/entry_411.xml"),
        ("Entry 418: ", "gt/entry_418.xml", "ha/entry_418.xml"),
        ("Entry 432: ", "gt/entry_432.xml", "ha/entry_432.xml"),
        ("Entry 435: ", "gt/entry_435.xml", "ha/entry_435.xml"),
        ("Entry 440: ", "gt/entry_440.xml", "ha/entry_440.xml"),
        ("Entry 451: ", "gt/entry_451.xml", "ha/entry_451.xml"),
        ("Entry 457: ", "gt/entry_457.xml", "ha/entry_457.xml"),
        ("Entry 467: ", "gt/entry_467.xml", "ha/entry_467.xml"),
        ("Entry 475: ", "gt/entry_475.xml", "ha/entry_475.xml"),
        ("Entry 485: ", "gt/entry_485.xml", "ha/entry_485.xml"),
        ("Entry 493: ", "gt/entry_493.xml", "ha/entry_493.xml"),
        ("Entry 511: ", "gt/entry_511.xml", "ha/entry_511.xml"),
        ("Entry 515: ", "gt/entry_515.xml", "ha/entry_515.xml"),
        ("Entry 529: ", "gt/entry_529.xml", "ha/entry_529.xml"),
        ("Entry 531: ", "gt/entry_531.xml", "ha/entry_531.xml"),
        ("Entry 540: ", "gt/entry_540.xml", "ha/entry_540.xml"),
        ("Entry 549: ", "gt/entry_549.xml", "ha/entry_549.xml"),
        ("Entry 552: ", "gt/entry_552.xml", "ha/entry_552.xml"),
        ("Entry 566: ", "gt/entry_566.xml", "ha/entry_566.xml"),
        ("Entry 584: ", "gt/entry_584.xml", "ha/entry_584.xml"),
    ]

    # CASES = [
    #     ("Entry 6: ", "gt/entry_6.xml", "btg/entry_6.xml"),
    #     ("Entry 32: ", "gt/entry_32.xml", "btg/entry_32.xml"),
    #     ("Entry 41: ", "gt/entry_41.xml", "btg/entry_41.xml"),
    #     ("Entry 50: ", "gt/entry_50.xml", "btg/entry_50.xml"),
    #     ("Entry 58: ", "gt/entry_58.xml", "btg/entry_58.xml"),
    #     ("Entry 64: ", "gt/entry_64.xml", "btg/entry_64.xml"),
    #     ("Entry 73: ", "gt/entry_73.xml", "btg/entry_73.xml"),
    #     ("Entry 77: ", "gt/entry_77.xml", "btg/entry_77.xml"),
    #     ("Entry 89: ", "gt/entry_89.xml", "btg/entry_89.xml"),
    #     ("Entry 92: ", "gt/entry_92.xml", "btg/entry_92.xml"),
    #     ("Entry 108: ", "gt/entry_108.xml", "btg/entry_108.xml"),
    #     ("Entry 109: ", "gt/entry_109.xml", "btg/entry_109.xml"),
    #     ("Entry 124: ", "gt/entry_124.xml", "btg/entry_124.xml"),
    #     ("Entry 150: ", "gt/entry_150.xml", "btg/entry_150.xml"),
    #     ("Entry 155: ", "gt/entry_155.xml", "btg/entry_155.xml"),
    #     ("Entry 194: ", "gt/entry_194.xml", "btg/entry_194.xml"),
    #     ("Entry 220: ", "gt/entry_220.xml", "btg/entry_220.xml"),
    #     ("Entry 248: ", "gt/entry_248.xml", "btg/entry_248.xml"),
    #     ("Entry 256: ", "gt/entry_256.xml", "btg/entry_256.xml"),
    #     ("Entry 266: ", "gt/entry_266.xml", "btg/entry_266.xml"),
    #     ("Entry 272: ", "gt/entry_272.xml", "btg/entry_272.xml"),
    #     ("Entry 317: ", "gt/entry_317.xml", "btg/entry_317.xml"),
    #     ("Entry 321: ", "gt/entry_321.xml", "btg/entry_321.xml"),
    #     ("Entry 325: ", "gt/entry_325.xml", "btg/entry_325.xml"),
    #     ("Entry 345: ", "gt/entry_345.xml", "btg/entry_345.xml"),
    #     ("Entry 371: ", "gt/entry_371.xml", "btg/entry_371.xml"),
    #     ("Entry 375: ", "gt/entry_375.xml", "btg/entry_375.xml"),
    #     ("Entry 376: ", "gt/entry_376.xml", "btg/entry_376.xml"),
    #     ("Entry 380: ", "gt/entry_380.xml", "btg/entry_380.xml"),
    #     ("Entry 402: ", "gt/entry_402.xml", "btg/entry_402.xml"),
    #     ("Entry 411: ", "gt/entry_411.xml", "btg/entry_411.xml"),
    #     ("Entry 418: ", "gt/entry_418.xml", "btg/entry_418.xml"),
    #     ("Entry 432: ", "gt/entry_432.xml", "btg/entry_432.xml"),
    #     ("Entry 435: ", "gt/entry_435.xml", "btg/entry_435.xml"),
    #     ("Entry 440: ", "gt/entry_440.xml", "btg/entry_440.xml"),
    #     ("Entry 451: ", "gt/entry_451.xml", "btg/entry_451.xml"),
    #     ("Entry 457: ", "gt/entry_457.xml", "btg/entry_457.xml"),
    #     ("Entry 467: ", "gt/entry_467.xml", "btg/entry_467.xml"),
    #     ("Entry 475: ", "gt/entry_475.xml", "btg/entry_475.xml"),
    #     ("Entry 485: ", "gt/entry_485.xml", "btg/entry_485.xml"),
    #     ("Entry 493: ", "gt/entry_493.xml", "btg/entry_493.xml"),
    #     ("Entry 511: ", "gt/entry_511.xml", "btg/entry_511.xml"),
    #     ("Entry 515: ", "gt/entry_515.xml", "btg/entry_515.xml"),
    #     ("Entry 529: ", "gt/entry_529.xml", "btg/entry_529.xml"),
    #     ("Entry 531: ", "gt/entry_531.xml", "btg/entry_531.xml"),
    #     ("Entry 540: ", "gt/entry_540.xml", "btg/entry_540.xml"),
    #     ("Entry 549: ", "gt/entry_549.xml", "btg/entry_549.xml"),
    #     ("Entry 552: ", "gt/entry_552.xml", "btg/entry_552.xml"),
    #     ("Entry 566: ", "gt/entry_566.xml", "btg/entry_566.xml"),
    #     ("Entry 584: ", "gt/entry_584.xml", "btg/entry_584.xml"),
    # ]

    results = []
    for title, path_a, path_b in CASES:
        report = run_case(title, path_a, path_b, args.output, args.mode,
                          args.max_leaves, args.ignore_name_style)
        results.append((title.strip().rstrip(":"), report))

    write_summary(results, args.output)

    print(f"\nFull report written to {Path(args.output).resolve()}")
