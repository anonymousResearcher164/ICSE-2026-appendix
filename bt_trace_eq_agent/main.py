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

from bt_trace_equivalence import BTEquivalenceAgent

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


def run_case(title: str, path_a: str, path_b: str, output_path: str = DEFAULT_OUTPUT_PATH):
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
        agent = BTEquivalenceAgent()
        report = agent.check(xml_a, xml_b, mode="exhaustive")
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


if __name__ == "__main__":
    # Start each run with a fresh report file rather than appending forever.
    Path(DEFAULT_OUTPUT_PATH).write_text("", encoding="utf-8")

    # Edit these to point at your own BT XML files.
    CASES = [
        ("Entry 41: ",
         "trees/entry_41_gt.xml", "trees/entry_41_syn.xml"),
        ("Entry 64: ",
         "trees/entry_64_gt.xml", "trees/entry_64_syn.xml"),
        ("Entry 150: ",
         "trees/entry_150_gt.xml", "trees/entry_150_syn.xml"),
        ("Entry 256: ",
         "trees/entry_256_gt.xml", "trees/entry_256_syn.xml"),
        ("Entry 317: ",
         "trees/entry_317_gt.xml", "trees/entry_317_syn.xml"),
        ("Entry 321: ",
         "trees/entry_321_gt.xml", "trees/entry_321_syn.xml"),
        ("Entry 402: ",
         "trees/entry_402_gt.xml", "trees/entry_402_syn.xml"),
        ("Entry 493: ",
         "trees/entry_493_gt.xml", "trees/entry_493_syn.xml"),
        ("Entry 511: ",
         "trees/entry_511_gt.xml", "trees/entry_511_syn.xml"),
        ("Entry 552: ",
         "trees/entry_552_gt.xml", "trees/entry_552_syn.xml"),
    ]

    for title, path_a, path_b in CASES:
        run_case(title, path_a, path_b)

    print(f"\nFull report written to {Path(DEFAULT_OUTPUT_PATH).resolve()}")
