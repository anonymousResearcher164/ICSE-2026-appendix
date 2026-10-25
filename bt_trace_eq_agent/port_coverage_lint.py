#!/usr/bin/env python3
"""
port_coverage_lint.py
=====================

Gates a behavior-tree *description* against the ports of the tree it
describes, so quantitative information cannot be silently dropped on the
way to natural language.

The problem it exists to catch
-------------------------------
A dictionary template that renders

    <RetryUntilSuccesful num_attempts="5">  ...  </RetryUntilSuccesful>
    <RetryUntilSuccesful num_attempts="10"> ...  </RetryUntilSuccesful>

as "keep trying until it succeeds" has thrown away both bounds. Nothing
downstream can recover them: a tree regenerated from that description is
free to emit num_attempts="-1", which in BehaviorTree.CPP means *retry
forever*. A robot that gives up after 5 attempts and one that never gives
up are different systems, and trace equivalence cannot tell them apart --
an attempt count is invisible within a single tick, so a trace-level
checker correctly ignores it. That is exactly why this gate is separate
from trace equivalence rather than folded into it: the two measure
different things, and blending them would muddy a definition that is
otherwise easy to state and check.

What it checks
--------------
For every node in the ground-truth tree that carries a semantically
load-bearing port with a *literal* value, the port's value must be
recoverable from the description. Two tiers:

  REQUIRED  control-flow and timing ports on control/decorator nodes
            (attempt counts, cycle counts, retry budgets, thresholds,
            durations, rates, distances). A miss fails the lint.
  ADVISORY  any other port with a literal value -- magnitudes on leaf
            actions such as <Wait wait_duration="5"/>. Reported but not
            failing, since whether these belong in a description is a
            dictionary-design choice. --strict promotes them to REQUIRED.

A port whose value is a blackboard remapping ("{goal}") is data flow, not
a magnitude: there is no number to preserve, so it is reported as DEFERRED
and never fails. This is what keeps the lint quiet about the port plumbing
(path="{path}", object_cloud="{cloud}") that dominates real trees while
still catching wait_duration="5".

Value rendering
---------------
A description does not have to quote the raw attribute. Accepted forms are
derived per port kind:

  attempts/count   "5", "five"; -1 must appear as an *unbounded* phrase
                   ("indefinitely", "forever", "without limit"), since
                   that is what -1 means and a bare "-1" in prose is not
                   a description of it
  threshold        same, except -1 means "all children", so an
                   all-quantifier phrase is required instead
  duration         the literal, plus the unit-converted form: a port
                   named *_msec with value 1000 also accepts "1 second",
                   because rendering milliseconds as seconds is a
                   reasonable template choice, not a loss
  rate/distance    the literal, with trailing-zero variants ("1.0"/"1")

Usage
-----
    # one pair
    python3 port_coverage_lint.py trees/entry_493_gt.xml descriptions/entry_493.txt

    # a whole corpus: entry_<N>_gt.xml  <->  entry_<N>.txt
    python3 port_coverage_lint.py --batch trees/ descriptions/

    # what a v6 dictionary has to be able to express, per tree
    python3 port_coverage_lint.py --inventory trees/

    # see the gate fail a lossy description and pass a complete one
    python3 port_coverage_lint.py --demo

Exit status is 1 if any REQUIRED port is unaccounted for, so this can gate
a corpus in CI.
"""

from __future__ import annotations

import argparse
import glob
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# What counts as load-bearing
# --------------------------------------------------------------------------- #

# kind -> how the value is allowed to surface in prose. See module docstring.
KIND_ATTEMPTS = "attempts"     # -1 == retry/repeat forever
KIND_THRESHOLD = "threshold"   # -1 == "all children" (Parallel)
KIND_DURATION = "duration"     # milliseconds or seconds
KIND_RATE = "rate"
KIND_DISTANCE = "distance"
KIND_COUNT = "count"
KIND_VALUE = "value"           # compared literally (blackboard rules, cases)

# Ports whose loss changes what the tree *does*, keyed by XML tag. Spelling
# variants are listed separately rather than normalized, because the
# dataset contains the circulating misspelling "RetryUntilSuccesful" and a
# lint that silently accepted only the correct spelling would pass a tree
# whose bounds it never looked at.
REQUIRED_PORTS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "RetryUntilSuccessful":  (("num_attempts", KIND_ATTEMPTS),),
    "RetryUntilSuccesful":   (("num_attempts", KIND_ATTEMPTS),),
    "Retry":                 (("num_attempts", KIND_ATTEMPTS),),
    "RepeatUntilSuccess":    (("num_attempts", KIND_ATTEMPTS),),
    "Repeat":                (("num_cycles", KIND_ATTEMPTS),),
    "Loop":                  (("num_cycles", KIND_ATTEMPTS),),
    "KeepRunningUntilFailure": (),
    "RecoveryNode":          (("number_of_retries", KIND_ATTEMPTS),),
    "Parallel":              (("success_count", KIND_THRESHOLD),
                              ("failure_count", KIND_THRESHOLD)),
    "ParallelNode":          (("success_count", KIND_THRESHOLD),
                              ("failure_count", KIND_THRESHOLD)),
    "Timeout":               (("msec", KIND_DURATION),),
    "Delay":                 (("delay_msec", KIND_DURATION),),
    "RateController":        (("hz", KIND_RATE),),
    "DistanceController":    (("distance", KIND_DISTANCE),),
    "SpeedController":       (("min_rate", KIND_RATE), ("max_rate", KIND_RATE),
                              ("min_speed", KIND_RATE), ("max_speed", KIND_RATE)),
    "BlackboardCheckInt":    (("value_A", KIND_VALUE), ("value_B", KIND_VALUE)),
    "BlackboardCheckDouble": (("value_A", KIND_VALUE), ("value_B", KIND_VALUE)),
    "BlackboardCheckString": (("value_A", KIND_VALUE), ("value_B", KIND_VALUE)),
}
# Switch2..Switch6 take `variable` plus case_1..case_N.
for _n in range(2, 7):
    REQUIRED_PORTS[f"Switch{_n}"] = (("variable", KIND_VALUE),) + tuple(
        (f"case_{_i}", KIND_VALUE) for _i in range(1, _n + 1)
    )

# Attributes that are never a magnitude: identity, bookkeeping, or the XML
# plumbing that BehaviorTree.CPP itself defines.
IGNORED_ATTRS = {"ID", "name", "BTCPP_format", "main_tree_to_execute",
                 "_description", "_subtreeID"}

# Declaration blocks, not node instances: <TreeNodesModel> states what ports
# a custom node *has*, with defaults. Linting those would demand prose for
# port declarations that no tick ever reads.
SKIPPED_CONTAINERS = {"TreeNodesModel"}

TIER_REQUIRED = "REQUIRED"
TIER_ADVISORY = "ADVISORY"

# Deliberately narrow. A phrase like "repeatedly until" does NOT mean
# unbounded -- "retry repeatedly until it succeeds, up to 5 times" is
# bounded -- and accepting it here would let a bounded description satisfy
# a num_attempts="-1" port, which is the exact confusion this lint exists
# to prevent.
UNBOUNDED_PHRASES = ("indefinitely", "forever", "without limit", "no limit",
                     "unbounded", "infinite", "infinitely", "never gives up",
                     "never give up", "as many times as needed",
                     "until it eventually succeeds")
ALL_CHILDREN_PHRASES = ("all children", "all of the children", "every child",
                        "all actions", "all of the actions", "all must",
                        "all succeed", "all complete")
NUMBER_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
                5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve"}


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PortUse:
    """One (node, port, value) occurrence in a tree."""
    tag: str
    node_name: str
    port: str
    raw: str
    kind: str
    tier: str

    @property
    def is_deferred(self) -> bool:
        """True when the value is a blackboard remapping, e.g. "{goal}"."""
        v = self.raw.strip()
        return v.startswith("{") and v.endswith("}")

    def __str__(self) -> str:
        label = f"<{self.tag}>"
        if self.node_name and self.node_name != self.tag:
            label += f" '{self.node_name}'"
        return f'{label} {self.port}="{self.raw}"'


@dataclass
class Finding:
    use: PortUse
    status: str                      # PASS | FAIL | DEFERRED
    accepted: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class CoverageReport:
    tree_path: str
    description_path: str
    findings: List[Finding]

    def _of(self, tier: str, status: str) -> List[Finding]:
        return [f for f in self.findings
                if f.use.tier == tier and f.status == status]

    @property
    def required_total(self) -> int:
        return len([f for f in self.findings
                    if f.use.tier == TIER_REQUIRED and f.status != "DEFERRED"])

    @property
    def required_passed(self) -> int:
        return len(self._of(TIER_REQUIRED, "PASS"))

    @property
    def failures(self) -> List[Finding]:
        return self._of(TIER_REQUIRED, "FAIL")

    @property
    def coverage(self) -> float:
        total = self.required_total
        return 1.0 if total == 0 else self.required_passed / total

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self, verbose: bool = False) -> str:
        lines = [f"{os.path.basename(self.tree_path)}  vs  "
                 f"{os.path.basename(self.description_path)}"]
        if self.required_total == 0:
            lines.append("  no REQUIRED ports with literal values -- "
                         "nothing to lose")
        else:
            lines.append(f"  port coverage: {self.required_passed}/"
                         f"{self.required_total} ({self.coverage:.0%})")
        for f in self.findings:
            if f.status == "PASS" and not verbose:
                continue
            if f.status == "DEFERRED" and not verbose:
                continue
            mark = {"PASS": "ok  ", "FAIL": "MISS", "DEFERRED": "defer"}[f.status]
            lines.append(f"    [{mark}] {f.use}  ({f.use.tier})")
            if f.note:
                lines.append(f"           {f.note}")
            if f.status == "FAIL" and f.accepted:
                shown = ", ".join(repr(a) for a in f.accepted[:6])
                lines.append(f"           description must contain one of: {shown}")
        advisory_miss = self._of(TIER_ADVISORY, "FAIL")
        if advisory_miss and not verbose:
            lines.append(f"    ({len(advisory_miss)} advisory port(s) also "
                         f"unaccounted for; -v to list, --strict to enforce)")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Scanning a tree
# --------------------------------------------------------------------------- #

def read_xml(path: str) -> str:
    """Read a tree, tolerating a BOM and leading blank lines (as main.py does)."""
    return pathlib.Path(path).read_text(encoding="utf-8-sig").strip()


def scan_tree(xml_text: str, strict: bool = False) -> List[PortUse]:
    """
    Collect every load-bearing port occurrence in `xml_text`.

    Walks *all* <BehaviorTree> elements, not just the main one, so ports
    inside subtree definitions are covered too -- a description of the tree
    has to account for them if the subtree is part of what runs.
    """
    root = ET.fromstring(xml_text)
    uses: List[PortUse] = []

    def walk(elem: ET.Element) -> None:
        if elem.tag in SKIPPED_CONTAINERS:
            return
        required = REQUIRED_PORTS.get(elem.tag, ())
        required_names = {port for port, _ in required}
        name = elem.attrib.get("name") or elem.attrib.get("ID") or elem.tag
        for port, kind in required:
            if port in elem.attrib:
                uses.append(PortUse(elem.tag, name, port, elem.attrib[port],
                                    kind, TIER_REQUIRED))
        for attr, value in elem.attrib.items():
            if attr in IGNORED_ATTRS or attr in required_names:
                continue
            uses.append(PortUse(
                elem.tag, name, attr, value, KIND_VALUE,
                TIER_REQUIRED if strict else TIER_ADVISORY))
        for child in elem:
            walk(child)

    if root.tag == "root":
        for bt in root.findall("BehaviorTree"):
            for child in bt:
                walk(child)
    elif root.tag == "BehaviorTree":
        for child in root:
            walk(child)
    else:
        walk(root)
    return uses


# --------------------------------------------------------------------------- #
# Matching a value against prose
# --------------------------------------------------------------------------- #

def _numeric_variants(raw: str) -> List[str]:
    """Surface forms a number may legitimately take in prose."""
    out = [raw]
    try:
        f = float(raw)
    except ValueError:
        return out
    if f.is_integer():
        i = int(f)
        out.append(str(i))                       # "1.0" -> "1"
        if i in NUMBER_WORDS:
            out.append(NUMBER_WORDS[i])          # "5"   -> "five"
    else:
        out.append(("%g" % f))
    return out


def _duration_variants(port: str, raw: str) -> List[str]:
    """
    Durations may be rendered in the unit the reader expects: a port named
    *_msec holding 1000 is faithfully described as "1 second".
    """
    out = _numeric_variants(raw)
    try:
        value = float(raw)
    except ValueError:
        return out
    if "msec" in port or "_ms" in port:
        seconds = value / 1000.0
        out.extend(_numeric_variants(
            str(int(seconds)) if seconds.is_integer() else "%g" % seconds))
    elif "sec" in port:
        out.extend(_numeric_variants("%g" % (value * 1000)))
    return out


def accepted_forms(use: PortUse) -> List[str]:
    """The strings any of which, if present, accounts for this port."""
    return list(dict.fromkeys(_accepted_forms(use)))   # de-duped, order kept


def _accepted_forms(use: PortUse) -> List[str]:
    raw = use.raw.strip()
    if use.kind == KIND_ATTEMPTS and raw == "-1":
        return list(UNBOUNDED_PHRASES)
    if use.kind == KIND_THRESHOLD and raw == "-1":
        return list(ALL_CHILDREN_PHRASES)
    if use.kind == KIND_DURATION:
        return _duration_variants(use.port, raw)
    if use.kind in (KIND_ATTEMPTS, KIND_COUNT, KIND_THRESHOLD,
                    KIND_RATE, KIND_DISTANCE):
        return _numeric_variants(raw)
    return [raw]


def _contains_token(haystack: str, needle: str) -> bool:
    """
    Substring match for phrases, but whole-token match for bare numbers, so
    an attempt count of 5 is not satisfied by the "5" inside "15" or "0.5".
    """
    needle = needle.strip().lower()
    if not needle:
        return False
    if re.fullmatch(r"-?\d+(\.\d+)?", needle):
        return re.search(rf"(?<![\d.]){re.escape(needle)}(?![\d.])",
                         haystack) is not None
    return needle in haystack


def check_description(uses: Sequence[PortUse], description: str) -> List[Finding]:
    haystack = description.lower()
    findings: List[Finding] = []
    for use in uses:
        if use.is_deferred:
            key = use.raw.strip()[1:-1]
            noted = _contains_token(haystack, key)
            findings.append(Finding(
                use, "DEFERRED",
                note=(f"blackboard-bound; no magnitude to preserve"
                      + ("" if noted else f" (key '{key}' not mentioned)"))))
            continue
        forms = accepted_forms(use)
        hit = next((f for f in forms if _contains_token(haystack, f)), None)
        if hit is not None:
            findings.append(Finding(use, "PASS", forms, f"matched {hit!r}"))
        else:
            note = ""
            if use.kind == KIND_ATTEMPTS and use.raw.strip() == "-1":
                note = ("-1 means unbounded; the description must say so "
                        "rather than quoting -1")
            elif use.kind == KIND_THRESHOLD and use.raw.strip() == "-1":
                note = "-1 means all children must meet the threshold"
            findings.append(Finding(use, "FAIL", forms, note))
    return findings


def lint(tree_path: str, description_path: str, strict: bool = False) -> CoverageReport:
    uses = scan_tree(read_xml(tree_path), strict=strict)
    description = pathlib.Path(description_path).read_text(encoding="utf-8-sig")
    return CoverageReport(tree_path, description_path,
                          check_description(uses, description))


# --------------------------------------------------------------------------- #
# Inventory: what a dictionary must be able to express
# --------------------------------------------------------------------------- #

def inventory(trees_dir: str, strict: bool = False) -> str:
    lines: List[str] = []
    totals: Dict[Tuple[str, str], int] = {}
    for path in sorted(glob.glob(os.path.join(trees_dir, "*.xml"))):
        try:
            uses = [u for u in scan_tree(read_xml(path), strict=strict)
                    if u.tier == TIER_REQUIRED and not u.is_deferred]
        except ET.ParseError as exc:
            lines.append(f"{os.path.basename(path)}: PARSE ERROR: {exc}")
            continue
        if not uses:
            continue
        lines.append(f"{os.path.basename(path)}")
        for u in uses:
            lines.append(f"    {u}")
            totals[(u.tag, u.port)] = totals.get((u.tag, u.port), 0) + 1
    lines.append("")
    lines.append("Ports a v6 template must bind, by frequency:")
    for (tag, port), n in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"    {n:3}  <{tag}> {port}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Demo: the entry_493 regression, before and after
# --------------------------------------------------------------------------- #

DEMO_TREE = """<root main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <FallbackStar name="root_AutoLocalization">
      <initialPoseReceived/>
      <SequenceStar name="doSelfLocalization">
        <RetryUntilSuccesful num_attempts="5" name="retry_client_request">
          <globalLocalizationServiceRequest/>
        </RetryUntilSuccesful>
        <RetryUntilSuccesful num_attempts="10" name="retry_localization">
          <Sequence><IsLocalized/></Sequence>
        </RetryUntilSuccesful>
      </SequenceStar>
    </FallbackStar>
  </BehaviorTree>
</root>
"""

DEMO_V5 = """If the initial pose has already been received, the behavior
succeeds immediately. Otherwise, perform self-localization in order: keep
trying the global localization service request until it succeeds, then keep
trying to localize until it succeeds."""

DEMO_V6 = """If the initial pose has already been received, the behavior
succeeds immediately. Otherwise, perform self-localization in order: execute
the global localization service request, retrying up to 5 attempts in total
before failing; then attempt localization, retrying up to 10 attempts in
total before failing."""


def demo() -> int:
    """
    Run the gate over one tree and two descriptions of it. Exit status
    reflects whether the *demonstration* held -- v5 must fail and v6 must
    pass -- not whether the v5 text passed.
    """
    import tempfile
    print("Port-coverage gate: entry_493, bounded retries (5 and 10)")
    print()
    results = {}
    with tempfile.TemporaryDirectory() as d:
        tree = os.path.join(d, "entry_493_gt.xml")
        pathlib.Path(tree).write_text(DEMO_TREE, encoding="utf-8")
        for key, label, text in (
                ("v5", "v5 template (attempt count not carried)", DEMO_V5),
                ("v6", "v6 template (attempt count bound)", DEMO_V6)):
            desc = os.path.join(d, "entry_493.txt")
            pathlib.Path(desc).write_text(text, encoding="utf-8")
            report = lint(tree, desc)
            print(f"--- {label} ---")
            print(report.summary(verbose=True))
            print(f"    verdict: {'PASS' if report.ok else 'FAIL'}")
            print()
            results[key] = report.ok
    as_expected = results.get("v5") is False and results.get("v6") is True
    print("gate behaves as intended: v5 rejected, v6 accepted"
          if as_expected else
          f"UNEXPECTED: v5 ok={results.get('v5')}, v6 ok={results.get('v6')}")
    return 0 if as_expected else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _entry_id(path: str) -> Optional[str]:
    m = re.search(r"(entry_\d+)", os.path.basename(path))
    return m.group(1) if m else None


def run_batch(trees_dir: str, desc_dir: str, strict: bool, verbose: bool,
              allow_missing: bool = False) -> int:
    trees = sorted(glob.glob(os.path.join(trees_dir, "*_gt.xml"))) or \
            sorted(glob.glob(os.path.join(trees_dir, "*.xml")))
    if not trees:
        print(f"No tree XML found in {trees_dir}", file=sys.stderr)
        return 2
    failed = missing = 0
    cov_sum = cov_n = 0.0
    for tree in trees:
        eid = _entry_id(tree)
        candidates = [os.path.join(desc_dir, f"{eid}.txt"),
                      os.path.join(desc_dir, f"{eid}.md")] if eid else []
        desc = next((c for c in candidates if os.path.isfile(c)), None)
        if desc is None:
            print(f"{os.path.basename(tree)}: no description found "
                  f"({', '.join(os.path.basename(c) for c in candidates) or 'n/a'})")
            missing += 1
            continue
        report = lint(tree, desc, strict=strict)
        print(report.summary(verbose=verbose))
        if report.required_total:
            cov_sum += report.coverage
            cov_n += 1
        if not report.ok:
            failed += 1
    print()
    if cov_n:
        print(f"mean port coverage: {cov_sum / cov_n:.0%} over {int(cov_n)} tree(s)")
    print(f"{failed} tree(s) with unaccounted REQUIRED ports"
          + (f", {missing} without a description" if missing else ""))
    # A tree with no description is an incomplete corpus, not a pass: left
    # as exit 0 this gate would green-light a run in which nothing was
    # actually checked. --allow-missing is for bootstrapping, when the
    # descriptions do not exist yet.
    if missing and not allow_missing:
        print("FAIL: missing descriptions (pass --allow-missing to tolerate)")
        return 1
    return 1 if failed else 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Check that a BT description preserves the tree's "
                    "load-bearing port values.")
    p.add_argument("tree", nargs="?", help="ground-truth BT XML")
    p.add_argument("description", nargs="?", help="its natural-language description")
    p.add_argument("--batch", nargs=2, metavar=("TREES_DIR", "DESC_DIR"),
                   help="lint a corpus, pairing entry_<N>_gt.xml with entry_<N>.txt")
    p.add_argument("--inventory", metavar="TREES_DIR",
                   help="list the load-bearing ports a dictionary must express")
    p.add_argument("--demo", action="store_true",
                   help="show the gate failing a lossy description and passing a complete one")
    p.add_argument("--allow-missing", action="store_true",
                   help="in --batch, do not fail on trees that have no description yet")
    p.add_argument("--strict", action="store_true",
                   help="promote ADVISORY ports (leaf magnitudes) to REQUIRED")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="list passing and deferred ports too")
    args = p.parse_args(argv[1:])

    if args.demo:
        return demo()
    if args.inventory:
        print(inventory(args.inventory, strict=args.strict))
        return 0
    if args.batch:
        return run_batch(args.batch[0], args.batch[1], args.strict,
                         args.verbose, args.allow_missing)
    if not (args.tree and args.description):
        p.print_help()
        return 2
    report = lint(args.tree, args.description, strict=args.strict)
    print(report.summary(verbose=args.verbose))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
