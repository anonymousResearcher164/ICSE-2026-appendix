#!/usr/bin/env python3
"""
bt_trace_equivalence.py
========================

A small agent that checks whether two behavior trees (BehaviorTree.CPP-style
XML) are *execution-trace equivalent*.

Definition used here
---------------------
Two trees are trace-equivalent if, for every scenario (an assignment of
SUCCESS/FAILURE outcomes to every Condition/Action leaf name that appears in
either tree), ticking both trees from the root produces:

  1. The same final root status (SUCCESS / FAILURE / RUNNING), AND
  2. The same ordered sequence of (node_type, node_name, status) tuples
     visited during that tick.

This is stricter than "same final result" -- it also requires the same
control-flow path (e.g. a Sequence short-circuiting at the same child),
which is what you actually want when comparing a generated tree against
a reference tree, or two LLM-generated trees against each other.

Because leaf outcomes are not fixed in the XML (conditions/actions are
implemented in C++ normally), this agent enumerates or samples the leaf
outcome space and checks equivalence scenario-by-scenario. For trees with
few distinct leaf names this can be exhaustive (2^N scenarios); for larger
trees it falls back to random sampling with a configurable budget.

Supported BehaviorTree.CPP node types
--------------------------------------
Control flow : Sequence, SequenceStar, Fallback (a.k.a. Selector), FallbackStar,
               ReactiveSequence, ReactiveFallback, Parallel, RecoveryNode (Nav2),
               PipelineSequence (Nav2), RoundRobin (Nav2), ConditionalSequence
               (undocumented/generator-specific name, treated as a 2-child
               Sequence)
Decorators   : Inverter, ForceSuccess, ForceFailure, Repeat, RetryUntilSuccessful
               (and the known misspelling RetryUntilSuccesful), Delay, Timeout,
               KeepRunningUntilFailure, RateController (Nav2), Decorator and
               ObjectExtractionCondition (undocumented/generator- or domain-
               specific names, treated as pass-through)
Leaves       : Condition, Action, SubTree, OR any tag with no children --
               this covers both the generic-tag-with-ID convention
               (<Action ID="PickObject"/>) and the custom-tag convention
               (<PickObject/>) used by some generators. A non-leaf tag that
               isn't recognized as control/decorator raises a clear error
               rather than being silently mis-ticked as a leaf.

Trace identity note
--------------------
Each trace event is (kind, name, status). `kind` is "Leaf" for every leaf
node regardless of its raw XML tag (since the same leaf can legally be
written <Action ID="X"/> or <X/> depending on convention -- comparing raw
tags here would flag semantically identical leaves as divergent). For
control/decorator nodes, `kind` is the actual tag (e.g. "Sequence",
"RecoveryNode"). `name` is always the ID/name attribute (or the tag itself
if no such attribute exists).

Usage
-----
    from bt_trace_equivalence import BTEquivalenceAgent

    agent = BTEquivalenceAgent()
    report = agent.check(xml_tree_a, xml_tree_b, mode="exhaustive")
    print(report.summary())

Or from the command line:

    python3 bt_trace_equivalence.py tree_a.xml tree_b.xml
"""

from __future__ import annotations

import itertools
import random
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple, Optional


# --------------------------------------------------------------------------- #
# Status model
# --------------------------------------------------------------------------- #

class Status(Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RUNNING = "RUNNING"

    def __str__(self):
        return self.value


TraceEvent = Tuple[str, str, Status, bool]  # (node_type, node_name, status, has_explicit_name)
Scenario = Dict[str, Status]          # leaf_name -> forced outcome


# --------------------------------------------------------------------------- #
# Tree node (in-memory, parsed from XML)
# --------------------------------------------------------------------------- #

@dataclass
class BTNode:
    tag: str                      # XML tag, e.g. "Sequence", "Condition", "MoveTo"
    name: str                     # ID/name attribute, falls back to tag
    attrib: Dict[str, str] = field(default_factory=dict)
    children: List["BTNode"] = field(default_factory=list)
    has_explicit_name: bool = False  # True iff the XML itself gave a name/ID
    # attribute, as opposed to `name` being auto-filled from the tag during
    # parsing. Used to decide whether a name difference on a control/
    # decorator node is a meaningful authoring choice (compare it) or just
    # an artifact of one file leaving the node unnamed (ignore it).

    LEAF_TAGS = {"Condition", "Action", "SubTree"}
    CONTROL_TAGS = {"Sequence", "Fallback", "Selector",
                     "ReactiveSequence", "ReactiveFallback", "Parallel",
                     # Memory-node variants (BehaviorTree.CPP "Star" naming /
                     # some generators' synonyms): for a *single* tick they
                     # behave identically to their plain counterparts -- the
                     # "memory" only changes behavior across repeated ticks.
                     "SequenceStar", "FallbackStar",
                     # Nav2 BT node: ticks child[0]; on FAILURE, ticks
                     # child[1] (the recovery action), then retries child[0].
                     "RecoveryNode",
                     # Nav2 BT node: re-ticks previous children when a later
                     # child returns RUNNING. For a single simulated tick
                     # (no RUNNING modeled mid-tree), this is observationally
                     # identical to a plain Sequence: tick children in
                     # order, stop and return on first non-SUCCESS.
                     "PipelineSequence",
                     # NOT a documented BehaviorTree.CPP/Nav2 node -- appears
                     # to be a custom/generator-specific name. Best-guess
                     # semantics, confirmed with the user: behaves like a
                     # 2-child Sequence (tick child[0]; only tick child[1]
                     # if child[0] succeeded).
                     "ConditionalSequence",
                     # Nav2 BT node: ticks children in round-robin fashion
                     # across multiple ticks, remembering which child to
                     # resume from. With no persisted "current child" state
                     # in a single simulated tick, the defensible single-
                     # tick view starts from child[0]: tick children in
                     # order, stop and return on first non-FAILURE result;
                     # FAILURE only if every child fails. Equivalent to a
                     # plain Fallback within one tick.
                     "RoundRobin"}
    DECORATOR_TAGS = {"Inverter", "ForceSuccess", "ForceFailure",
                       "Repeat", "RetryUntilSuccessful",
                       # Known misspelling that has circulated in real
                       # BehaviorTree.CPP-adjacent code/discussion (missing
                       # the second 's' in "Successful"). Treated as an
                       # alias, not a separate node type.
                       "RetryUntilSuccesful",
                       "Delay", "Timeout",
                       # BehaviorTree.CPP decorator: ticks the child; if it
                       # FAILS, returns FAILURE; if it SUCCEEDS or is
                       # RUNNING, returns RUNNING. NOT a pass-through --
                       # SUCCESS gets converted to RUNNING.
                       "KeepRunningUntilFailure",
                       # Nav2 decorator: throttles tick rate for its child;
                       # returns RUNNING when not actually ticking the
                       # child (rate-limited). Single-tick simulation has no
                       # rate state to consult, so the most defensible
                       # assumption is that the child IS ticked this time --
                       # same treatment as Delay: pass the child's status
                       # straight through.
                       "RateController",
                       # NOT a real BehaviorTree.CPP/Nav2 tag -- appears to
                       # be a generator using the base class name
                       # "DecoratorNode" literally as the XML tag. Best-
                       # guess semantics, confirmed with the user: simple
                       # pass-through (tick the child, return its status
                       # unchanged), same as Delay/Timeout.
                       "Decorator",
                       # Custom/domain-specific node (not a generic
                       # base-class name like "Decorator" -- this is its own
                       # registered tag, presumably wrapping a real
                       # condition-check-then-act behavior in its C++
                       # implementation). Has exactly 1 child despite the
                       # "Condition" suffix in its name, which is atypical
                       # for BT.CPP conditions (normally childless leaves).
                       # Best-guess semantics, confirmed with the user:
                       # pass-through (tick the child, return its status
                       # unchanged).
                       "ObjectExtractionCondition"}

    def is_control(self) -> bool:
        return self.tag in self.CONTROL_TAGS

    def is_decorator(self) -> bool:
        return self.tag in self.DECORATOR_TAGS

    def is_leaf(self) -> bool:
        # Structural, not tag-based: a node with children is NEVER a leaf,
        # even if its tag isn't one we explicitly recognize as control/
        # decorator. This matters for custom-tag leaves like <Action_A/>
        # (no children -> correctly a leaf) versus an unrecognized control
        # node that does have children (must not silently degrade to a
        # leaf -- see tick()'s dispatch, which raises instead).
        return not self.children

    def leaf_names(self) -> List[str]:
        """Collect distinct leaf identifiers under this subtree."""
        names: List[str] = []
        if self.is_leaf():
            names.append(self.name)
        for c in self.children:
            names.extend(c.leaf_names())
        return names


# --------------------------------------------------------------------------- #
# XML parsing
# --------------------------------------------------------------------------- #

def parse_bt_xml(xml_text: str) -> BTNode:
    """
    Parse a BehaviorTree.CPP XML string and return the root BTNode of the
    *main* tree (the <BehaviorTree> whose ID matches root_main, or the first
    <BehaviorTree> if unspecified).
    """
    root_elem = ET.fromstring(xml_text)

    if root_elem.tag != "root":
        # Allow passing a bare <BehaviorTree> fragment too
        bt_elem = root_elem if root_elem.tag == "BehaviorTree" else None
    else:
        main_id = root_elem.attrib.get("main_tree_to_execute")
        bt_elems = root_elem.findall("BehaviorTree")
        if not bt_elems:
            raise ValueError("No <BehaviorTree> element found in XML")
        bt_elem = None
        if main_id:
            for b in bt_elems:
                if b.attrib.get("ID") == main_id:
                    bt_elem = b
                    break
        if bt_elem is None:
            bt_elem = bt_elems[0]

    if bt_elem is None:
        raise ValueError("Could not locate a <BehaviorTree> root to parse")

    # A <BehaviorTree> wraps exactly one real root child (often a control node)
    real_children = list(bt_elem)
    if not real_children:
        raise ValueError("<BehaviorTree> element has no child nodes")

    return _elem_to_node(real_children[0])


def _elem_to_node(elem: ET.Element) -> BTNode:
    explicit_name = elem.attrib.get("name") or elem.attrib.get("ID")
    name = explicit_name or elem.tag
    node = BTNode(
        tag=elem.tag,
        name=name,
        attrib=dict(elem.attrib),
        has_explicit_name=bool(explicit_name),
    )
    for child in elem:
        node.children.append(_elem_to_node(child))
    return node


# --------------------------------------------------------------------------- #
# Tick engine
# --------------------------------------------------------------------------- #

class TraceCollectingTicker:
    """
    Ticks a BTNode tree once, against a fixed Scenario (leaf-name -> Status),
    recording an ordered trace of every node visited.

    Notes on semantics (kept intentionally simple/standard):
      * Sequence / ReactiveSequence / SequenceStar / PipelineSequence: tick
        children in order; on first non-SUCCESS, stop and return that
        status. (PipelineSequence's "re-tick previous children on RUNNING"
        behavior only differs from plain Sequence across multiple ticks,
        which this single-tick simulator doesn't model anyway.)
      * Fallback / Selector / ReactiveFallback / FallbackStar: tick children
        in order; on first non-FAILURE, stop and return that status.
      * RecoveryNode (Nav2, exactly 2 children): tick child[0] (main
        action); if it doesn't fail, return that status. If it fails, tick
        child[1] (recovery action), then retry child[0] once and return
        that result.
      * Parallel: ticks ALL children (no short-circuit), success/failure
        thresholds default to "all must succeed" / "any may fail" unless
        success_count/failure_count attributes are given.
      * Inverter: flips SUCCESS<->FAILURE of its single child (RUNNING passes
        through).
      * ForceSuccess / ForceFailure: always returns the forced status, but
        still ticks (and records) its child first.
      * Repeat / RetryUntilSuccessful (and the known misspelling
        RetryUntilSuccesful): modeled as a single tick of the child for
        trace purposes (multi-tick looping isn't observable in one tick).
      * Delay / Timeout: pass the child's status straight through; the
        time-based behavior (waiting before ticking, or failing on timeout)
        isn't observable within a single simulated tick.
      * KeepRunningUntilFailure: NOT a pass-through. Child FAILURE ->
        FAILURE; child SUCCESS or RUNNING -> RUNNING (it keeps re-ticking
        the child until the child eventually fails).
      * Leaf (Condition/Action/anything unrecognized): status comes directly
        from the scenario dict; defaults to FAILURE if the name isn't present
        (so unseen leaves don't silently succeed).
    """

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.trace: List[TraceEvent] = []

    def tick(self, node: BTNode) -> Status:
        if node.is_leaf():
            # Structural leaves (no children) ALWAYS go through _tick_leaf,
            # regardless of tag. This correctly handles both XML
            # conventions seen in practice:
            #   <Action ID="Action_A"/>   (generic tag + ID attribute)
            #   <Action_A/>               (behavior name used as the tag)
            status = self._tick_leaf(node)
        elif node.is_control():
            status = self._tick_control(node)
        elif node.is_decorator():
            status = self._tick_decorator(node)
        else:
            # Has children, but isn't a tag we recognize as control or
            # decorator. Ticking it as a leaf would silently skip its
            # children (the bug that previously made RecoveryNode /
            # FallbackStar collapse to opaque single-step leaves). Fail
            # loudly instead so unmodeled node types are visible and fixable
            # rather than producing a misleading "divergence".
            raise ValueError(
                f"Unrecognized non-leaf node tag '{node.tag}' (name={node.name!r}) "
                f"with {len(node.children)} child/children. Add it to "
                f"BTNode.CONTROL_TAGS/DECORATOR_TAGS and implement its tick "
                f"semantics, or confirm it should be treated as a leaf."
            )

        # Trace identity uses a normalized "kind" label, not the raw tag,
        # because the same logical node can be written with different tags
        # depending on XML convention (see comment above). Using the raw
        # tag here would make semantically identical leaves register as
        # different trace events purely due to authoring style.
        kind = self._trace_kind(node)
        trace_name = self._trace_name(node)
        explicit = node.is_leaf() or node.has_explicit_name
        self.trace.append((kind, trace_name, status, explicit))
        return status

    @staticmethod
    def _trace_kind(node: BTNode) -> str:
        """Stable category label for trace comparison purposes."""
        if node.is_leaf():
            return "Leaf"
        return node.tag

    @staticmethod
    def _trace_name(node: BTNode) -> str:
        """
        Name used for trace comparison: always the node's real name/ID (or
        the tag, if neither was given in the XML).

        Note: this method intentionally does NOT decide whether an
        auto-generated name should be ignored during comparison -- that
        decision is asymmetric (it depends on ground truth's naming status,
        not this node's own tree), so it can't be made correctly while
        ticking a single tree in isolation. See `_normalize_trace_for_comparison`,
        which applies that rule positionally after both traces are collected.
        """
        return node.name

    # -- control flow -----------------------------------------------------

    def _tick_control(self, node: BTNode) -> Status:
        tag = node.tag

        if tag in ("Sequence", "ReactiveSequence", "SequenceStar", "PipelineSequence",
                    "ConditionalSequence"):
            for child in node.children:
                s = self.tick(child)
                if s != Status.SUCCESS:
                    return s
            return Status.SUCCESS

        if tag in ("Fallback", "Selector", "ReactiveFallback", "FallbackStar", "RoundRobin"):
            for child in node.children:
                s = self.tick(child)
                if s != Status.FAILURE:
                    return s
            return Status.FAILURE

        if tag == "RecoveryNode":
            # Nav2 semantics: child[0] is the main action, child[1] is the
            # recovery action. Tick child[0]; if it doesn't fail, return
            # immediately. If it fails, tick the recovery (child[1]), then
            # retry child[0] once more and return that result. (Nav2 also
            # bounds this with a retry count across multiple ticks, but for
            # a single simulated tick this one-retry view is the observable
            # behavior.)
            if len(node.children) < 2:
                raise ValueError(
                    f"RecoveryNode '{node.name}' must have exactly 2 children "
                    f"(main action, recovery action); found {len(node.children)}"
                )
            main_child, recovery_child = node.children[0], node.children[1]
            s = self.tick(main_child)
            if s != Status.FAILURE:
                return s
            self.tick(recovery_child)
            return self.tick(main_child)

        if tag == "Parallel":
            results = [self.tick(c) for c in node.children]
            n = len(results)
            success_needed = int(node.attrib.get("success_count", n))
            failure_needed = int(node.attrib.get("failure_count", 1))
            successes = sum(1 for r in results if r == Status.SUCCESS)
            failures = sum(1 for r in results if r == Status.FAILURE)
            if successes >= success_needed:
                return Status.SUCCESS
            if failures >= failure_needed:
                return Status.FAILURE
            return Status.RUNNING

        raise ValueError(f"Unhandled control node tag: {tag}")

    # -- decorators ---------------------------------------------------------

    def _tick_decorator(self, node: BTNode) -> Status:
        if not node.children:
            raise ValueError(f"Decorator {node.name} has no child")
        child_status = self.tick(node.children[0])
        tag = node.tag

        if tag == "Inverter":
            if child_status == Status.SUCCESS:
                return Status.FAILURE
            if child_status == Status.FAILURE:
                return Status.SUCCESS
            return Status.RUNNING

        if tag == "ForceSuccess":
            return Status.RUNNING if child_status == Status.RUNNING else Status.SUCCESS

        if tag == "ForceFailure":
            return Status.RUNNING if child_status == Status.RUNNING else Status.FAILURE

        if tag in ("Repeat", "RetryUntilSuccessful", "RetryUntilSuccesful"):
            # Single-tick view: pass the child's result through.
            # ("RetryUntilSuccesful" is a known real-world misspelling --
            # treated as the same node, not a separate type.)
            return child_status

        if tag == "KeepRunningUntilFailure":
            # NOT a pass-through: child FAILURE -> FAILURE; child SUCCESS
            # or RUNNING -> RUNNING (the decorator keeps re-ticking the
            # child until it eventually fails).
            return Status.FAILURE if child_status == Status.FAILURE else Status.RUNNING

        if tag in ("Delay", "Timeout", "RateController", "Decorator",
                    "ObjectExtractionCondition"):
            # Delay: waits a fixed duration before ticking the child, then
            # passes its status through. Timeout: lets the child run up to
            # a duration limit, returning FAILURE if it times out (not
            # observable in a single tick) or the child's status otherwise.
            # RateController (Nav2): throttles tick rate, returning RUNNING
            # when not ticking the child this cycle -- with no rate state to
            # consult in a single simulated tick, assume the child IS ticked.
            # Decorator / ObjectExtractionCondition: not real BT.CPP/Nav2
            # tags, best-guess pass-through (see CONTROL_TAGS/DECORATOR_TAGS
            # comment).
            # All five reduce to "pass the child's status through".
            return child_status

        raise ValueError(f"Unhandled decorator tag: {tag}")

    # -- leaves -------------------------------------------------------------

    def _tick_leaf(self, node: BTNode) -> Status:
        return self.scenario.get(node.name, Status.FAILURE)


# --------------------------------------------------------------------------- #
# Equivalence checking
# --------------------------------------------------------------------------- #

def _normalize_traces_for_comparison(
    trace_a: List[TraceEvent], trace_b: List[TraceEvent]
) -> Tuple[List[Tuple[str, str, Status]], List[Tuple[str, str, Status]]]:
    """
    Produce comparison-only views of two traces, applying the rule:
    a node's name is ignored (replaced with a stable placeholder) whenever
    GROUND TRUTH (tree A)'s corresponding node has no explicit name/ID --
    regardless of whether tree B's node at that position is named. This is
    intentionally asymmetric: an unnamed GT node compared against ANY synth
    name (named or unnamed) is treated as a non-difference, since the only
    information being compared in that case is "did the synthesis tool
    bother to label this container" -- not a logic difference.

    Only applied where both traces have a node at that position (positions
    are compared elementwise, by index); a length mismatch or a kind/status
    difference still surfaces normally since this function only touches the
    name field.
    """
    norm_a: List[Tuple[str, str, Status]] = []
    norm_b: List[Tuple[str, str, Status]] = []
    for i in range(min(len(trace_a), len(trace_b))):
        kind_a, name_a, status_a, explicit_a = trace_a[i]
        kind_b, name_b, status_b, explicit_b = trace_b[i]
        if not explicit_a:
            # Ground truth left this node unnamed -> ignore name on both
            # sides at this position, regardless of tree B's naming.
            norm_a.append((kind_a, "#unnamed", status_a))
            norm_b.append((kind_b, "#unnamed", status_b))
        else:
            norm_a.append((kind_a, name_a, status_a))
            norm_b.append((kind_b, name_b, status_b))
    # Trailing extra entries on the longer side are kept as-is (with real
    # names) so a genuine length/structure mismatch still shows up.
    for i in range(len(norm_a), len(trace_a)):
        kind, name, status, _ = trace_a[i]
        norm_a.append((kind, name, status))
    for i in range(len(norm_b), len(trace_b)):
        kind, name, status, _ = trace_b[i]
        norm_b.append((kind, name, status))
    return norm_a, norm_b


def _step_accuracy(trace_a: List[TraceEvent], trace_b: List[TraceEvent]) -> float:
    """
    Fraction of positions where the two traces agree, using the same
    GT-aware name normalization as equivalence checking (so a name-only
    difference that the equivalence rule ignores doesn't count against
    accuracy either).

    Denominator is the LONGER of the two traces, so a trace that's too
    short (stops early) is penalized for the steps it's missing, not just
    the steps it got wrong. An empty/empty pair (both traces have zero
    steps) is defined as 1.0 (vacuously identical).

    NOTE: this is strictly positional. A single inserted/removed/reordered
    step early in the trace shifts every subsequent index, so this metric
    can drop sharply even when most of the same events occur in the same
    relative order. See `_order_tolerant_similarity` for a metric that
    isn't sensitive to that.
    """
    cmp_a, cmp_b = _normalize_traces_for_comparison(trace_a, trace_b)
    total = max(len(cmp_a), len(cmp_b))
    if total == 0:
        return 1.0
    matches = sum(1 for i in range(min(len(cmp_a), len(cmp_b))) if cmp_a[i] == cmp_b[i])
    return matches / total


def _order_tolerant_similarity(trace_a: List[TraceEvent], trace_b: List[TraceEvent]) -> float:
    """
    Longest Common Subsequence (LCS) based similarity, tolerant of
    insertions, deletions, and reordering -- unlike `_step_accuracy`, a
    single step appearing earlier or later doesn't cascade into mismatches
    for everything that follows it; the LCS simply skips the misaligned
    element and keeps matching the rest.

    Score = 2 * |LCS(A, B)| / (|A| + |B|), the standard normalized LCS
    similarity (1.0 when traces are identical as sequences, 0.0 when they
    share no common subsequence, including the empty-vs-nonempty case).
    Both traces are 1.0-vs-1.0 (i.e. 1.0 overall) when both are empty.

    Uses the same GT-aware name normalization as `_step_accuracy` so the
    two metrics are computed on comparable footing.

    Complexity is O(len(A) * len(B)) per scenario -- fine for the trace
    lengths behavior trees produce (tens of nodes), but this is why it's
    a separate, optional metric rather than folded into the main loop
    unconditionally for very large trees.
    """
    cmp_a, cmp_b = _normalize_traces_for_comparison(trace_a, trace_b)
    n, m = len(cmp_a), len(cmp_b)
    if n == 0 and m == 0:
        return 1.0
    if n == 0 or m == 0:
        return 0.0

    # Standard LCS length via DP, single row to keep memory O(min(n, m)).
    if m < n:
        cmp_a, cmp_b = cmp_b, cmp_a
        n, m = m, n
    prev = [0] * (n + 1)
    for j in range(1, m + 1):
        curr = [0] * (n + 1)
        bj = cmp_b[j - 1]
        for i in range(1, n + 1):
            if cmp_a[i - 1] == bj:
                curr[i] = prev[i - 1] + 1
            else:
                curr[i] = max(prev[i], curr[i - 1])
        prev = curr
    lcs_len = prev[n]

    return (2.0 * lcs_len) / (n + m)


# --------------------------------------------------------------------------- #
# Equivalence checking

# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Equivalence scale (qualitative tiers over the order-tolerant similarity score)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EquivalenceTier:
    emoji: str
    label: str
    low: float   # inclusive lower bound, as a fraction (0.0-1.0)
    high: float  # inclusive upper bound, as a fraction (0.0-1.0)
    description: str
    use_case: str

    def __str__(self) -> str:
        return f"{self.emoji} {self.label}"


# Tiers as specified by the user. Boundaries are inclusive on both ends per
# tier as given (100, 95-99, 80-94, 50-79, 0-49); a score of exactly 1.0
# (100%) only ever comes from `step_accuracy` (strict positional identity),
# since `similarity` (LCS-based) only reads 1.0 when the traces are
# identical AS SEQUENCES too -- which coincides with strict identity, so
# the boundary is unambiguous in practice.
EQUIVALENCE_SCALE: List[EquivalenceTier] = [
    EquivalenceTier(
        "\U0001F534", "Strict Trace Equivalence", 1.0, 1.0,
        "Identical execution traces across all tick intervals -- exact "
        "step-by-step match of active nodes, return statuses, and sequence.",
        "Safety-critical robotics, medical systems, deterministic regression testing.",
    ),
    EquivalenceTier(
        "\U0001F7E1", "Functional Equivalence", 0.95, 0.999999,
        "Identical end-state objectives with minor temporal or structural "
        "variations (e.g. duration differences, redundant-branch handling).",
        "Game AI testing, industrial automation, validation of refactored trees.",
    ),
    EquivalenceTier(
        "\U0001F7E2", "Logic & Intent Equivalence", 0.80, 0.949999,
        "Fundamental decision-making logic and final goal are achieved, but "
        "paths diverge -- permutations of independent sub-trees or "
        "non-deterministic selector choices.",
        "RL agents, imitation learning evaluation, autonomous driving simulation.",
    ),
    EquivalenceTier(
        "\U0001F535", "Partial Sub-Tree Equivalence", 0.50, 0.799999,
        "Shared common behavioral patterns or localized routines, but "
        "diverge globally -- identical isolated sub-trees wrapped inside "
        "different global priority structures.",
        "Assessing skill reuse or modularity in evolutionary robotics.",
    ),
    EquivalenceTier(
        "\u26AB", "Behavioral Divergence", 0.0, 0.499999,
        "Independent behaviors with no meaningful trace correlation -- "
        "different action nodes triggered under the same conditions.",
        "Comparing entirely distinct agent personalities or unaligned models.",
    ),
]


def equivalence_tier(similarity: float, is_strict_equivalent: bool = False) -> EquivalenceTier:
    """
    Map a similarity score (0.0-1.0) to its qualitative tier on the
    Behavior Tree Equivalence Scale.

    `is_strict_equivalent`, when True, forces the 100% Strict Trace
    Equivalence tier regardless of the numeric similarity passed in --
    use this when the trees were already confirmed positionally identical
    (the equivalence check's own pass/fail), since that's a stronger and
    more direct signal than re-deriving it from a similarity score.
    """
    if is_strict_equivalent:
        return EQUIVALENCE_SCALE[0]
    for tier in EQUIVALENCE_SCALE[1:]:
        if tier.low <= similarity <= tier.high:
            return tier
    # similarity < 0 shouldn't happen, but fall back to the bottom tier
    # rather than raising, since this is a reporting/labeling function.
    return EQUIVALENCE_SCALE[-1]


@dataclass
class Mismatch:
    scenario: Scenario
    final_status_a: Status
    final_status_b: Status
    trace_a: List[TraceEvent]
    trace_b: List[TraceEvent]
    step_accuracy: float = 1.0
    similarity: float = 1.0

    def explain(self) -> str:
        lines = [f"Scenario: { {k: str(v) for k, v in self.scenario.items()} }"]
        lines.append(f"  Tree A final status: {self.final_status_a}")
        lines.append(f"  Tree B final status: {self.final_status_b}")
        lines.append(
            f"  Step accuracy: {self.step_accuracy:.1%}  |  "
            f"Order-tolerant similarity: {self.similarity:.1%} "
            f"({equivalence_tier(self.similarity).label})"
        )
        lines.append("  Trace A: " + _fmt_trace(self.trace_a))
        lines.append("  Trace B: " + _fmt_trace(self.trace_b))

        # Pinpoint first diverging step using the SAME normalization rule
        # used to decide equivalence, so the reported divergence point is
        # never a name-only difference that the rule says doesn't count.
        cmp_a, cmp_b = _normalize_traces_for_comparison(self.trace_a, self.trace_b)
        for i, (ea, eb) in enumerate(zip(cmp_a, cmp_b)):
            if ea != eb:
                raw_a = self.trace_a[i][:3]
                raw_b = self.trace_b[i][:3]
                lines.append(f"  First divergence at step {i}: A={raw_a} vs B={raw_b}")
                break
        else:
            if len(cmp_a) != len(cmp_b):
                lines.append(
                    f"  Traces share a common prefix but differ in length "
                    f"({len(self.trace_a)} vs {len(self.trace_b)} steps)"
                )
        return "\n".join(lines)


def _fmt_trace(trace: List[TraceEvent]) -> str:
    return " -> ".join(f"{name}:{status}" for (_, name, status, _explicit) in trace)


@dataclass
class EquivalenceReport:
    equivalent: bool
    scenarios_checked: int
    mode: str
    leaf_names: List[str]
    mismatches: List[Mismatch] = field(default_factory=list)
    average_step_accuracy: float = 1.0
    min_step_accuracy: float = 1.0
    average_similarity: float = 1.0
    min_similarity: float = 1.0

    @property
    def tier(self) -> EquivalenceTier:
        """
        Overall qualitative tier for this comparison. Uses the WORST
        (minimum) similarity across all scenarios checked, not the
        average -- one badly-diverging scenario should pull the overall
        classification down rather than being smoothed out by many
        trivially-matching scenarios, since the worst case is usually what
        matters for a correctness judgment.
        """
        return equivalence_tier(self.min_similarity, is_strict_equivalent=self.equivalent)

    def summary(self, max_mismatches: int = 3) -> str:
        lines = []
        verdict = "FULLY EQUIVALENT" if self.equivalent else "NOT FULLY EQUIVALENT"
        lines.append(f"Result: {verdict}")
        lines.append(f"Mode: {self.mode}  |  Scenarios checked: {self.scenarios_checked}")
        lines.append(f"Leaf names considered: {', '.join(self.leaf_names) or '(none)'}")
        lines.append(
            f"Average step accuracy: {self.average_step_accuracy:.1%}  "
            f"(worst scenario: {self.min_step_accuracy:.1%})"
        )
        lines.append(
            f"Average order-tolerant similarity: {self.average_similarity:.1%}  "
            f"(worst scenario: {self.min_similarity:.1%})"
        )
        tier = self.tier
        lines.append(f"Equivalence tier (worst-case): {tier.emoji} {tier.label}")
        lines.append(f"  {tier.description}")
        lines.append(f"  Typical use case: {tier.use_case}")
        if self.mismatches:
            lines.append(f"\nShowing {min(max_mismatches, len(self.mismatches))} of "
                         f"{len(self.mismatches)} mismatching scenario(s):\n")
            for m in self.mismatches[:max_mismatches]:
                lines.append(m.explain())
                lines.append("")
        return "\n".join(lines)


class BTEquivalenceAgent:
    """
    The agent. Parses two BT XML documents, derives the joint leaf-name
    space, generates scenarios, ticks both trees per scenario, and compares
    traces.
    """

    def __init__(self, seed: Optional[int] = 42):
        self._rng = random.Random(seed)

    # -- public API -----------------------------------------------------

    def check(
        self,
        xml_a: str,
        xml_b: str,
        mode: str = "auto",
        max_exhaustive_leaves: int = 14,
        sample_budget: int = 20_000,
        stop_on_first_mismatch: bool = False,
    ) -> EquivalenceReport:
        """
        mode: "exhaustive" | "sample" | "auto"
          auto -> exhaustive if joint leaf count <= max_exhaustive_leaves,
                  else sampled with `sample_budget` scenarios.
        """
        tree_a = parse_bt_xml(xml_a)
        tree_b = parse_bt_xml(xml_b)

        leaves = sorted(set(tree_a.leaf_names()) | set(tree_b.leaf_names()))

        if mode == "auto":
            mode = "exhaustive" if len(leaves) <= max_exhaustive_leaves else "sample"

        if mode == "exhaustive":
            scenarios = list(self._enumerate_scenarios(leaves))
        elif mode == "sample":
            n = min(sample_budget, 2 ** min(len(leaves), 24))
            scenarios = [self._random_scenario(leaves) for _ in range(n)]
        else:
            raise ValueError(f"Unknown mode: {mode!r}")

        mismatches: List[Mismatch] = []
        accuracy_sum = 0.0
        min_accuracy = 1.0
        similarity_sum = 0.0
        min_similarity = 1.0
        scenarios_run = 0
        for scenario in scenarios:
            ticker_a = TraceCollectingTicker(scenario)
            status_a = ticker_a.tick(tree_a)
            ticker_b = TraceCollectingTicker(scenario)
            status_b = ticker_b.tick(tree_b)

            cmp_a, cmp_b = _normalize_traces_for_comparison(ticker_a.trace, ticker_b.trace)
            accuracy = _step_accuracy(ticker_a.trace, ticker_b.trace)
            similarity = _order_tolerant_similarity(ticker_a.trace, ticker_b.trace)
            accuracy_sum += accuracy
            min_accuracy = min(min_accuracy, accuracy)
            similarity_sum += similarity
            min_similarity = min(min_similarity, similarity)
            scenarios_run += 1

            if status_a != status_b or cmp_a != cmp_b:
                mismatches.append(Mismatch(
                    scenario=scenario,
                    final_status_a=status_a,
                    final_status_b=status_b,
                    trace_a=ticker_a.trace,
                    trace_b=ticker_b.trace,
                    step_accuracy=accuracy,
                    similarity=similarity,
                ))
                if stop_on_first_mismatch:
                    break

        return EquivalenceReport(
            equivalent=(len(mismatches) == 0),
            scenarios_checked=len(scenarios),
            mode=mode,
            leaf_names=leaves,
            mismatches=mismatches,
            average_step_accuracy=(accuracy_sum / scenarios_run) if scenarios_run else 1.0,
            min_step_accuracy=min_accuracy,
            average_similarity=(similarity_sum / scenarios_run) if scenarios_run else 1.0,
            min_similarity=min_similarity,
        )

    # -- scenario generation ---------------------------------------------

    @staticmethod
    def _enumerate_scenarios(leaves: List[str]):
        outcomes = [Status.SUCCESS, Status.FAILURE]
        for combo in itertools.product(outcomes, repeat=len(leaves)):
            yield dict(zip(leaves, combo))

    def _random_scenario(self, leaves: List[str]) -> Scenario:
        return {name: self._rng.choice([Status.SUCCESS, Status.FAILURE]) for name in leaves}


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print("Usage: python3 bt_trace_equivalence.py <tree_a.xml> <tree_b.xml> [exhaustive|sample]")
        return 1

    path_a, path_b = argv[1], argv[2]
    mode = argv[3] if len(argv) > 3 else "auto"

    with open(path_a, "r", encoding="utf-8") as f:
        xml_a = f.read()
    with open(path_b, "r", encoding="utf-8") as f:
        xml_b = f.read()

    agent = BTEquivalenceAgent()
    report = agent.check(xml_a, xml_b, mode=mode)
    print(report.summary())
    return 0 if report.equivalent else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
