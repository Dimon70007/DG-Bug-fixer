#!/usr/bin/env python3
"""Phase state machine for a bug-fixer ledger (TASK-081 ST-2, TASK-085 Ж-2).

The ledger records findings; it cannot say which phase the run is in, and a hook
cannot infer it. This CLI owns the *transitions* between phases and appends each one to
the run journal — the only place the phase is recorded (spec §5.4, invariant 34):

    bugfixer_gate_state.py <ledger>                                   # print state
    bugfixer_gate_state.py <ledger> advance 2 --evidence "BASELINE: 1652/1654"
    bugfixer_gate_state.py <ledger> advance 3 --edge DEEPEN_SAME --evidence "..."
    bugfixer_gate_state.py <ledger> status PARTIAL
    bugfixer_gate_state.py <ledger> edges                             # list the eight

**Edges are named, not numbered.** Two different reasons lead from probe back to
research — ``QUEUE_EXHAUSTED`` (refuted, nothing left to probe) and ``DEEPEN_SAME``
(partially right, deepen this one). Keyed by ``(from, to)`` those two would
silently overwrite each other and the second meaning would exist only on paper, so
the key is the name and the phase pair is a field.

A phase is not a free variable: every edge carries a precondition read from the
ledger, and a refusal names the missing marker rather than saying "illegal".

Markers are anchored at the start of a line. A ledger that *describes* a marker
must not satisfy it: the same defect that made a prose mention of
``STATUS: BLOCKED`` block a finished task.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Final, NamedTuple

STATE_SUFFIX: Final[str] = ".bugfixer.jsonl"
"""Kept for callers that build the path themselves; the format lives in the journal module."""
VALID_PHASES: Final[tuple[int, ...]] = (1, 2, 3, 4, 5)
VALID_STATUSES: Final[tuple[str, ...]] = (
    "RUNNING",
    "BLOCKED",
    "DONE",
    "PARTIAL",
    "LIMIT_REACHED",
)

#: Marker-line prefix: list/quote/emphasis marks, but deliberately not ``|``, so a
#: table row cannot satisfy a precondition (invariant 17b).
M: Final[str] = r"^[ \t>*_-]{0,8}"

#: Optional per-ledger opt-out of the Phase-1 write ban, for ledgers that are not
#: bug-fix runs (tooling tasks). Anchored, like every other marker.
_PHASE_GATE_OFF_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t>*_-]{0,8}Phase gate:[ \t]*off\b", re.MULTILINE | re.IGNORECASE
)

#: A reproduction exists: a failing test was written and observed red.
_RED_TEST_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t>*_-]{0,8}(?:FAILING TEST|RED TEST|RED):", re.MULTILINE
)

#: The full-suite baseline was recorded before any change.
_BASELINE_RE: Final[re.Pattern[str]] = re.compile(M + r"BASELINE:", re.MULTILINE)

#: A clarification question pair from step 0.3.
_CLARIFICATION_Q_RE: Final[re.Pattern[str]] = re.compile(M + r"Q:", re.MULTILINE)

#: Fewer than three answers, with the gap named instead (step 0.3a).
_CLARIFICATION_GAP_RE: Final[re.Pattern[str]] = re.compile(M + r"Clarification gap:", re.MULTILINE)

#: Headless runs state assumptions instead of asking (step 0.4).
_ASSUMPTION_RE: Final[re.Pattern[str]] = re.compile(M + r"Assumption:", re.MULTILINE)

#: Phase 3 records that merging was checked, even when nothing was merged.
_MERGE_RE: Final[re.Pattern[str]] = re.compile(M + r"Merge:", re.MULTILINE)

#: A probe that reached a code change.
_ATTEMPT_RE: Final[re.Pattern[str]] = re.compile(M + r"Attempt\s+\d+\s+consumed\b", re.MULTILINE)

#: Proof that the target tests failed before the fix.
_RED_FACT_RE: Final[re.Pattern[str]] = re.compile(M + r"ФАКТ \(RED\):", re.MULTILINE)

#: Proof that the full suite is green after the fix.
_GREEN_FACT_RE: Final[re.Pattern[str]] = re.compile(M + r"ФАКТ \(GREEN\):", re.MULTILINE)

#: Logs collected in this cycle — without them "the mechanism is absent" is a guess.
_LOGS_FACT_RE: Final[re.Pattern[str]] = re.compile(M + r"ФАКТ \(логи\):", re.MULTILINE)

#: The bug nature, needed to tell a fixed flake from a fixed bug.
_BUG_RE: Final[re.Pattern[str]] = re.compile(M + r"Bug:[ \t]*(?:stable|flaky)\b", re.MULTILINE)

#: The diff was scanned; step 4.6a cannot be inferred from the diff itself.
_SECRETS_RE: Final[re.Pattern[str]] = re.compile(M + r"Secrets scan:", re.MULTILINE)

#: A named reason why one target test is enough.
_CORNER_RE: Final[re.Pattern[str]] = re.compile(M + r"Corner cases:[ \t]*none\b", re.MULTILINE)

#: Numeric progress: baseline, after, expected on the same line.
_DELTA_RE: Final[re.Pattern[str]] = re.compile(M + r"ФАКТ \(дельта\):", re.MULTILINE)

#: A named baseline test that the probe broke.
_REGRESSION_RE: Final[re.Pattern[str]] = re.compile(M + r"ФАКТ \(регрессия\):", re.MULTILINE)

#: The probe's changes were rolled back; lost without a line saying so.
_REVERT_RE: Final[re.Pattern[str]] = re.compile(M + r"Revert:", re.MULTILINE)

#: What a return to research learned.
_NEW_INFO_RE: Final[re.Pattern[str]] = re.compile(M + r"NEW INFORMATION:", re.MULTILINE)

#: The agent named the criterion when two metrics disagreed (step 4.7a).
_RESOLUTION_RE: Final[re.Pattern[str]] = re.compile(M + r"Resolution:", re.MULTILINE)

#: A markdown table row, to find the §11 results table header.
_TABLE_ROW_RE: Final[re.Pattern[str]] = re.compile(r"^\|.*\|[ \t]*$", re.MULTILINE)

#: A number, for counting the delta triple.
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"-?\d+(?:\.\d+)?")


def _load_gate() -> ModuleType:
    """Load the gate module for its ledger readers.

    The readers live in one module and are used from two: a second copy of the
    parser would drift from the first, and then the gate and the state machine
    would disagree about the same ledger.

    Returns:
        The imported ``bugfixer_gate`` module.
    """
    path = Path(__file__).resolve().with_name("bugfixer_gate.py")
    spec = importlib.util.spec_from_file_location("bugfixer_gate_shared", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load ledger readers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ledger_readers: Final[ModuleType] = _load_gate()
"""Shared readers: attempts, hypotheses, records, the active hypothesis."""

#: The journal owns the record format, so the CLI and the gate cannot disagree about it.
_journal = ledger_readers.journal


class Refusal(NamedTuple):
    """A refused transition, with the reason the agent needs to act on."""

    reason: str


def load(ledger: Path) -> dict[str, Any]:
    """Load the run state from the journal's last records.

    Args:
        ledger: Ledger file.

    Returns:
        State mapping with ``phase``, ``status``, and ``transitions``.

    Raises:
        SystemExit: If the journal exists but a record cannot be read.
    """
    try:
        return _journal.state_of(ledger)
    except _journal.JournalError as exc:
        print(f"bugfixer-state: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def count_markers(pattern: re.Pattern[str], ledger_text: str) -> int:
    """Count anchored occurrences of a marker.

    Args:
        pattern: Anchored marker pattern.
        ledger_text: Full ledger contents.

    Returns:
        Number of matches.
    """
    return len(pattern.findall(ledger_text))


# --- ledger-derived facts ----------------------------------------------------


def verdict_of_last_probe(ledger_text: str) -> str:
    """Verdict keyword of the most recent probe record.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        ``CONFIRMED``, ``REFUTED``, ``PARTIALLY``, ``DEGRADED``, or empty.
    """
    probes = [record for record in ledger_readers.parse_records(ledger_text) if record.kind == "probe"]
    if not probes:
        return ""
    outcome = probes[-1].outcome.upper()
    for keyword in ("CONFIRMED", "REFUTED", "PARTIALLY", "DEGRADED"):
        if keyword in outcome:
            return keyword
    return ""


def last_probe(ledger_text: str):
    """The most recent ``Kind: probe`` record.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        The record, or ``None`` when no probe has been made.
    """
    probes = [record for record in ledger_readers.parse_records(ledger_text) if record.kind == "probe"]
    return probes[-1] if probes else None


def attempt_count(ledger_text: str) -> int:
    """Attempts that reached a code change, or ``-1`` when a reset is unauthorized.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        The count, or ``-1`` when the ledger holds an agent-written reset.
    """
    try:
        return ledger_readers.count_attempts(ledger_text)
    except ledger_readers.CounterResetError:
        return -1


def active_hypothesis_id(ledger_text: str) -> str:
    """Active hypothesis, or ``"?"`` when the marker is ambiguous.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        Hypothesis id, ``""`` when absent, or ``"?"`` when two markers exist.
    """
    try:
        return ledger_readers.active_hypothesis(ledger_text)
    except ledger_readers.AmbiguousMarkerError:
        return "?"


def results_table_present(body: str) -> bool:
    """Whether a §11 results table with its required columns is present.

    The table is the only marker that is a table on purpose: its *columns* are the
    contract (``До``, ``После``, ``Порог``, ``Вывод``), not its first cell.

    Args:
        body: Record body.

    Returns:
        ``True`` when a header row carries every required column.
    """
    for row in _TABLE_ROW_RE.findall(body):
        normalized = row.lower()
        if all(column in normalized for column in ("до", "после", "порог", "вывод")):
            return True
    return False


def _rank_of(ledger_text: str, hypothesis_id: str) -> int:
    """Rank of a hypothesis, or ``0`` when absent or unranked.

    Args:
        ledger_text: Full ledger contents.
        hypothesis_id: Hypothesis id.

    Returns:
        The rank, or ``0``.
    """
    hypothesis = ledger_readers.parse_hypotheses(ledger_text).get(hypothesis_id)
    return hypothesis.rank if hypothesis else 0


# --- guards ------------------------------------------------------------------


def guard_reproduced(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``REPRODUCED`` 1→2: a red test, a baseline, and a clarified task.

    Clarification is a precondition rather than advice because a task understood
    wrongly produces a working solution to a different task. The gate cannot judge
    the answers, but it can require that the asking happened.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming the first missing marker, or ``None``.
    """
    del state
    if count_markers(_RED_TEST_RE, ledger_text) == 0:
        return Refusal(
            "cannot enter Phase 2: the ledger has no failing test. Write the RED test, "
            "run it, and record it on a line starting `FAILING TEST:` with the command "
            "and the observed failure."
        )
    if count_markers(_BASELINE_RE, ledger_text) == 0:
        return Refusal(
            "cannot enter Phase 2: the ledger has no baseline. Run the full suite before "
            "any change and record it on a line starting `BASELINE:`, listing what was "
            "already failing so a regression can be told from an old defect."
        )
    if count_markers(_ASSUMPTION_RE, ledger_text) > 0:
        return None
    pairs = count_markers(_CLARIFICATION_Q_RE, ledger_text)
    if 3 <= pairs <= 7:
        return None
    if 1 <= pairs <= 2 and count_markers(_CLARIFICATION_GAP_RE, ledger_text) > 0:
        return None
    return Refusal(
        "cannot enter Phase 2: task not clarified. Ask 3-7 questions in Phase 0 and record "
        "them as `Q:`/`A:` pairs under `## Clarifications`. With fewer than three answers, "
        f"name what is still unclear on a `Clarification gap:` line (found {pairs} pair(s), "
        "no gap line). Headless runs write `Assumption:` lines instead."
    )


def guard_already_tried(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``ALREADY_TRIED`` 2→3: a paid attempt exists and nothing is confirmed.

    Phase 2 no longer probes, so "the obvious version failed" cannot be observed;
    the observable equivalent is an attempt already on the record. History has
    priority over a fresh guess, which is why this edge is tested before
    ``FIRST_GUESS``.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal when there is no prior attempt, or the task is already solved.
    """
    del state
    attempts = attempt_count(ledger_text)
    if attempts < 0:
        return Refusal(
            "cannot enter research: the ledger has a `Counter reset:` line without the "
            "operator's authorization. Only the operator resets `A`, in the form "
            "`Counter reset: A -> 0 (by operator: <reason>)`."
        )
    if attempts == 0:
        return Refusal(
            "no previous attempt, so Phase 2 must create `H0` and go to Phase 4 "
            "(`FIRST_GUESS`). Research is for the case where the cheap guess is already "
            "spent; it has not been."
        )
    if verdict_of_last_probe(ledger_text) == "CONFIRMED":
        return Refusal(
            "the last probe is already CONFIRMED, so the task is solved. Go to Phase 5 "
            "(`PROVEN`) instead of researching a closed cause."
        )
    return None


def guard_first_guess(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``FIRST_GUESS`` 2→4: exactly one obvious hypothesis, already active.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming what is missing, or ``None``.
    """
    del state
    headings = ledger_readers.count_hypotheses(ledger_text)
    if headings != 1:
        return Refusal(
            f"Phase 2 creates exactly one `### H0`, found {headings}. Phase 2 makes one "
            "obvious guess; more than one is research (Phase 3), none is no progress."
        )
    hypothesis = ledger_readers.parse_hypotheses(ledger_text).get("H0")
    if hypothesis is None:
        return Refusal("Phase 2 must create `### H0 — <one-line assertion>`.")
    missing = [
        field
        for field, value in (
            ("Rank", hypothesis.rank),
            ("Proposed", hypothesis.proposed),
            ("Origin", hypothesis.origin),
            ("Mechanism", hypothesis.mechanism),
            ("Prediction", hypothesis.prediction),
        )
        if not value
    ]
    if missing:
        return Refusal(
            "`H0` is incomplete, missing: "
            + ", ".join(missing)
            + ". A hypothesis without Mechanism and Prediction cannot be tested; without "
            "Proposed it is not connected to the history."
        )
    records = {record.id for record in ledger_readers.parse_records(ledger_text)}
    if hypothesis.proposed not in records:
        return Refusal(
            f"`H0` says `Proposed | {hypothesis.proposed}`, but there is no such `### F<n>` "
            "block. Phase 2 writes a `Kind: route` record and points the hypothesis at it."
        )
    if attempt_count(ledger_text) > 0:
        return Refusal(
            "the task already has a paid attempt, so this is `ALREADY_TRIED` (2→3), not a "
            "first guess. Do not re-probe the obvious version."
        )
    if active_hypothesis_id(ledger_text) != "H0":
        return Refusal(
            "no active hypothesis: write `Working on: H0` when creating it. Phase 4 needs "
            "the marker to know which hypothesis an edit belongs to."
        )
    return None


def guard_hypothesis_ready(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``HYPOTHESIS_READY`` 3→4: an unchecked hypothesis is waiting and named.

    ``H0`` has already been probed by the time research runs, so the threshold is
    one *new* unchecked hypothesis, not two inside the phase.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming what is missing, or ``None``.
    """
    del state
    if count_markers(_MERGE_RE, ledger_text) == 0:
        return Refusal(
            "Phase 3 must record that merging was checked, on a `Merge:` line — "
            "`Merge: H2 ← H3 — same prediction` or `Merge: none — <why the predictions "
            "differ>`. Without it, duplicated hypotheses get probed twice."
        )
    unchecked = ledger_readers.unchecked_hypotheses(ledger_text)
    if not unchecked:
        return Refusal(
            "no unchecked hypothesis: research produced none. Produce at least one "
            "`### H<n>` with Rank, Mechanism and Prediction before entering Phase 4."
        )
    if ledger_readers.count_hypotheses(ledger_text) < 2:
        return Refusal(
            "a single hypothesis in the whole task means research added nothing: `H0` was "
            "already probed. Write the new hypothesis as `### H<n>`."
        )
    active = active_hypothesis_id(ledger_text)
    if active == "?":
        return Refusal(
            "the ledger has more than one `Working on:` line. Rewrite the single marker "
            "instead of appending another, so an edit cannot be attributed to the wrong "
            "hypothesis."
        )
    if not active:
        return Refusal(
            "no active hypothesis: name the one to probe on a `Working on: H<n>` line, "
            "picked by Rank (or the same `H` you just deepened)."
        )
    if active not in {hypothesis.id for hypothesis in unchecked}:
        return Refusal(
            f"`Working on: {active}` is not an unchecked hypothesis. Name one that still "
            "needs a probe, by `Rank`."
        )
    return None


def _skipped_without_goal(ledger_text: str) -> str:
    """Refuse ``SKIPPED`` that stands in for an unfinished probe (§5.1).

    A skipped hypothesis is legitimate only when another one reached ``CONFIRMED`` and the
    defect is closed — then a further probe is work without a target. Without that, ``SKIPPED``
    means "declined to check", and accepting it would turn the queue into a formality: eleven
    hypotheses, none probed, all marked skipped, run declared ``DONE``.

    Reading the last probe is not enough here: ``PROVEN`` already requires it to be
    ``CONFIRMED``, so that check would always pass. What matters is whether **any** hypothesis
    other than the skipped ones was confirmed, and the skip lines themselves.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        A refusal naming the first problem, or an empty string when every skip is justified.
    """
    parsed = ledger_readers.parse_hypotheses(ledger_text).values()
    skipped = [hypothesis for hypothesis in parsed if hypothesis.status == "SKIPPED"]
    if not skipped:
        return ""
    confirmed = [
        hypothesis.id
        for hypothesis in ledger_readers.parse_hypotheses(ledger_text).values()
        if hypothesis.status == "CONFIRMED"
    ]
    if not confirmed:
        return (
            f"{', '.join(hypothesis.id for hypothesis in skipped)} marked `SKIPPED`, but no "
            "hypothesis reached `CONFIRMED`. A skip is allowed only when the goal is already "
            "met by another hypothesis; without one it means the probe was declined, not that "
            "it was unnecessary. Probe the highest-ranked hypothesis, or record `REFUTED` if "
            "it is wrong."
        )
    reasonless = [
        hypothesis.id
        for hypothesis in skipped
        if not hypothesis.records[-1].reason.strip()
    ]
    if reasonless:
        return (
            f"{', '.join(reasonless)} marked `SKIPPED` without a reason. Write "
            "`- F<n> → SKIPPED — <what made it unnecessary>`; an unexplained skip reads as "
            "an omission, and the next session cannot tell the two apart."
        )
    return ""


def guard_proven(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``PROVEN`` 4→5: the verdict is backed by evidence mirrored per verdict type.

    The queue is deliberately not checked here: a ``CONFIRMED`` hypothesis closes the defect,
    and probing the rest afterwards is work without a target (§5.1). What **is** checked is
    that a skipped hypothesis was not a way of avoiding work: ``SKIPPED`` is allowed only when
    some hypothesis reached ``CONFIRMED``, which in this edge is the probe's own scope.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming the first missing proof, or ``None``.
    """
    del state
    verdict = verdict_of_last_probe(ledger_text)
    if verdict == "PARTIALLY":
        return Refusal(
            "`PARTIALLY CONFIRMED` is not a proof: it returns to Phase 3 by `DEEPEN_SAME`, "
            "not to Phase 5."
        )
    if verdict != "CONFIRMED":
        return Refusal(
            "cannot enter Phase 5: the last probe has no CONFIRMED verdict. Phase 5 only "
            "checks that something worked."
        )
    skipped_problem = _skipped_without_goal(ledger_text)
    if skipped_problem:
        return Refusal(skipped_problem)
    probe = last_probe(ledger_text)
    assert probe is not None
    if count_markers(_ATTEMPT_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: no `Attempt <k> consumed` line, so the production code "
            "was never changed (step 4.6). A fixed test is not a fixed bug."
        )
    if count_markers(_RED_FACT_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: no `ФАКТ (RED):` — nothing proves the target tests "
            "failed before the change."
        )
    if count_markers(_LOGS_FACT_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: no `ФАКТ (логи):`. Without logs the cycle says the fix "
            "worked but not why, and the next defect of the same class repeats."
        )
    if count_markers(_BUG_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: the bug nature is not named. Write `Bug: stable` or "
            "`Bug: flaky`; a flake and a bug are proved differently."
        )
    if count_markers(_GREEN_FACT_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: no `ФАКТ (GREEN):` — the full suite after the change is "
            "not recorded."
        )
    if count_markers(_SECRETS_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: no `Secrets scan:` line. Step 4.6a was skipped, and the "
            "gate cannot see the diff you wrote."
        )
    if not results_table_present(probe.body):
        return Refusal(
            "cannot enter Phase 5: no results table with the `До`, `После`, `Порог`, "
            "`Вывод` columns (§11). 'It works' without before and after cannot be checked."
        )
    target_tests = count_markers(_RED_TEST_RE, probe.body)
    if target_tests < 2 and count_markers(_CORNER_RE, probe.body) == 0:
        return Refusal(
            "cannot enter Phase 5: one target test is not a boundary. Add a second test or "
            "record `Corner cases: none — <why this behaviour has no edges>`."
        )
    return None


def guard_next_in_queue(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``NEXT_IN_QUEUE`` 4→4: refuted, and the next hypothesis is already named.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming the first missing proof, or ``None``.
    """
    del state
    attempts = attempt_count(ledger_text)
    if attempts < 0:
        return Refusal("the ledger has an unauthorized `Counter reset:` line.")
    if attempts >= ledger_readers.MAX_ATTEMPTS:
        return Refusal(
            f"attempt cap reached ({attempts} of {ledger_readers.MAX_ATTEMPTS}): write "
            "`STATUS: LIMIT_REACHED` with `## Report` (step 4.10)."
        )
    if verdict_of_last_probe(ledger_text) != "REFUTED":
        return Refusal(
            "`NEXT_IN_QUEUE` requires a REFUTED verdict. A partial hit is deepened by "
            "`DEEPEN_SAME`; a confirmed one goes to `PROVEN`."
        )
    probe = last_probe(ledger_text)
    assert probe is not None
    if count_markers(_LOGS_FACT_RE, probe.body) == 0:
        return Refusal(
            "refutation without `ФАКТ (логи):` is not a verdict: a silent test does not "
            "prove the mechanism is absent. Show in the logs that it is not there."
        )
    if count_markers(_REVERT_RE, probe.body) == 0:
        return Refusal(
            "a refuted probe must be rolled back before moving on: record `Revert:` with "
            "the files you restored (step 4.8)."
        )
    current_rank = _rank_of(ledger_text, probe.scope)
    waiting = [
        hypothesis
        for hypothesis in ledger_readers.unchecked_hypotheses(ledger_text)
        if hypothesis.rank > current_rank and hypothesis.id != probe.scope
    ]
    if not waiting:
        return Refusal(
            "the queue is empty, so the next step is `QUEUE_EXHAUSTED`: return to Phase 3 "
            "with `NEW INFORMATION:` and look for new hypotheses."
        )
    next_hypothesis = waiting[0]
    active = active_hypothesis_id(ledger_text)
    if active != next_hypothesis.id:
        return Refusal(
            "switch `Working on:` to the next hypothesis before this edge: expected "
            f"`Working on: {next_hypothesis.id}` (Rank {next_hypothesis.rank}), found "
            f"`{active or 'nothing'}`. Step 4.9a does that; step 4.0 only confirms it."
        )
    return None


def guard_queue_exhausted(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``QUEUE_EXHAUSTED`` 4→3: refuted with nothing left to probe.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming what is missing, or ``None``.
    """
    del state
    attempts = attempt_count(ledger_text)
    if attempts < 0:
        return Refusal("the ledger has an unauthorized `Counter reset:` line.")
    if attempts >= ledger_readers.MAX_ATTEMPTS:
        return Refusal(
            f"attempt cap reached ({attempts} of {ledger_readers.MAX_ATTEMPTS}): write "
            "`STATUS: LIMIT_REACHED` with `## Report` (step 4.10)."
        )
    if verdict_of_last_probe(ledger_text) != "REFUTED":
        return Refusal(
            "`QUEUE_EXHAUSTED` requires a REFUTED verdict; a partial hit goes back by "
            "`DEEPEN_SAME`."
        )
    probe = last_probe(ledger_text)
    assert probe is not None
    if count_markers(_LOGS_FACT_RE, probe.body) == 0:
        return Refusal("refutation without `ФАКТ (логи):` is not a verdict.")
    if count_markers(_REVERT_RE, probe.body) == 0:
        return Refusal("a refuted probe must be rolled back: record `Revert:` (step 4.8).")
    if ledger_readers.unchecked_hypotheses(ledger_text):
        return Refusal(
            "queue not empty: an unchecked hypothesis is already paid for. Use "
            "`NEXT_IN_QUEUE` instead of researching a case you have already formulated."
        )
    if count_markers(_NEW_INFO_RE, probe.body) == 0:
        return Refusal(
            "returning to research needs `NEW INFORMATION:` — what this cycle taught that "
            "makes a new hypothesis possible. A return that teaches nothing repeats the "
            "same round."
        )
    return None


def guard_deepen_same(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """``DEEPEN_SAME`` 4→3: partially right, so the same hypothesis is deepened.

    The queue is irrelevant here: a partial hit is deepened, not abandoned for the
    next hypothesis and not returned to research as if it had failed.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused).

    Returns:
        A refusal naming what is missing, or ``None``.
    """
    del state
    attempts = attempt_count(ledger_text)
    if attempts < 0:
        return Refusal("the ledger has an unauthorized `Counter reset:` line.")
    if attempts >= ledger_readers.MAX_ATTEMPTS:
        return Refusal(
            f"attempt cap reached ({attempts} of {ledger_readers.MAX_ATTEMPTS}): write "
            "`STATUS: LIMIT_REACHED` with `## Report` (step 4.10)."
        )
    if verdict_of_last_probe(ledger_text) != "PARTIALLY":
        return Refusal(
            "`DEEPEN_SAME` requires `PARTIALLY CONFIRMED`. A refuted hypothesis is dead "
            "(`NEXT_IN_QUEUE` or `QUEUE_EXHAUSTED`); a confirmed one goes to `PROVEN`."
        )
    probe = last_probe(ledger_text)
    assert probe is not None
    has_delta = count_markers(_DELTA_RE, probe.body) > 0
    has_regression = count_markers(_REGRESSION_RE, probe.body) > 0
    has_revert = count_markers(_REVERT_RE, probe.body) > 0
    if not has_delta and not has_regression:
        return Refusal(
            "progress needs evidence: either `ФАКТ (дельта):` with three numbers (baseline, "
            "after, expected) or `ФАКТ (регрессия): <named test>`. Unchanged numbers are "
            "`REFUTED`, not partial progress."
        )
    if has_delta:
        delta = _DELTA_RE.search(probe.body)
        assert delta is not None
        line = probe.body[delta.end() :].splitlines()[0] if probe.body[delta.end() :] else ""
        numbers = _NUMBER_RE.findall(line)
        if len(numbers) < 3:
            return Refusal(
                "`ФАКТ (дельта):` needs three numbers on that line — baseline, after, and "
                f"expected — found {len(numbers)}."
            )
        if has_revert:
            return Refusal(
                "`Revert:` is forbidden when a delta exists: the numbers moved, so the "
                "probe bought understanding. Rolling it back throws that away."
            )
    if not has_delta and not has_revert:
        return Refusal(
            "case C (regression without progress) must be rolled back: record `Revert:` "
            "with the files you restored."
        )
    if count_markers(_LOGS_FACT_RE, probe.body) == 0:
        return Refusal("a verdict without `ФАКТ (логи):` is not supported by evidence.")
    if count_markers(_NEW_INFO_RE, probe.body) == 0:
        return Refusal(
            "deepening the same hypothesis needs `NEW INFORMATION:` — what the probe added "
            "to `Mechanism` or `Prediction`."
        )
    if has_delta and has_regression and count_markers(_RESOLUTION_RE, probe.body) == 0:
        return Refusal(
            "metrics moved in opposite directions: name the criterion you prioritised on a "
            "`Resolution:` line (step 4.7a). The gate checks presence; a human checks the "
            "reasoning."
        )
    if active_hypothesis_id(ledger_text) != probe.scope:
        return Refusal(
            f"`DEEPEN_SAME` deepens the same hypothesis: keep `Working on: {probe.scope}`, "
            f"found `{active_hypothesis_id(ledger_text) or 'nothing'}`."
        )
    return None


# --- edges -------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    """One named transition between phases.

    The name is the key because the phase pair is not unique: ``QUEUE_EXHAUSTED``
    and ``DEEPEN_SAME`` both lead ``4→3`` for different reasons, with different
    verdicts and different proofs.

    Attributes:
        name: Why the transition happens, e.g. ``"QUEUE_EXHAUSTED"``.
        from_phase: Phase left.
        to_phase: Phase entered.
        guard: Precondition read from the ledger.
    """

    name: str
    from_phase: int
    to_phase: int
    guard: Callable[[str, dict[str, Any]], Refusal | None]


EDGES: Final[tuple[Edge, ...]] = (
    Edge("REPRODUCED", 1, 2, guard_reproduced),
    Edge("ALREADY_TRIED", 2, 3, guard_already_tried),
    Edge("FIRST_GUESS", 2, 4, guard_first_guess),
    Edge("HYPOTHESIS_READY", 3, 4, guard_hypothesis_ready),
    Edge("PROVEN", 4, 5, guard_proven),
    Edge("NEXT_IN_QUEUE", 4, 4, guard_next_in_queue),
    Edge("QUEUE_EXHAUSTED", 4, 3, guard_queue_exhausted),
    Edge("DEEPEN_SAME", 4, 3, guard_deepen_same),
)

#: Edge lookup by name. A phase-pair key could not hold both ``4→3`` edges.
EDGES_BY_NAME: Final[dict[str, Edge]] = {edge.name: edge for edge in EDGES}


def edges_between(current: int, target: int) -> list[Edge]:
    """Edges leading from ``current`` to ``target``.

    Args:
        current: Current phase.
        target: Requested phase.

    Returns:
        Matching edges, in declaration order.
    """
    return [edge for edge in EDGES if (edge.from_phase, edge.to_phase) == (current, target)]


def phase_gate_enabled(ledger_text: str) -> bool:
    """Whether the Phase-1 write ban applies to this ledger.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        ``False`` only when the ledger opts out with a `Phase gate: off` line.
    """
    return _PHASE_GATE_OFF_RE.search(ledger_text) is None


def advance(
    ledger: Path,
    target: int,
    *,
    evidence: str,
    edge: str | None = None,
) -> tuple[dict[str, Any] | None, Refusal | None]:
    """Move the ledger to ``target`` phase if the named edge is legal and proven.

    Args:
        ledger: Ledger file.
        target: Destination phase.
        evidence: Why the transition is justified; stored in history verbatim.
        edge: Edge name. Required when several edges share the phase pair — guessing
            would be exactly the silent substitution the skill forbids.

    Returns:
        ``(state, None)`` on success, or ``(None, refusal)`` when refused.
    """
    state = load(ledger)
    current = int(state.get("phase") or 1)

    if target not in VALID_PHASES:
        return None, Refusal(f"phase must be one of {VALID_PHASES}, got {target}.")

    if target == current and edge is None:
        # Re-setting the current phase is a no-op; only a named same-phase edge
        # (NEXT_IN_QUEUE) is a real transition and must be proved.
        return state, None

    candidates = edges_between(current, target)
    if not candidates:
        allowed = ", ".join(f"{item.name}({item.from_phase}->{item.to_phase})" for item in EDGES)
        return None, Refusal(
            f"illegal transition {current}->{target}. Named edges: {allowed}. "
            "Skipping a phase is the defect this guard exists to prevent."
        )

    chosen: Edge
    if edge is not None:
        matched = next((item for item in candidates if item.name == edge), None)
        if matched is None:
            names = ", ".join(item.name for item in candidates)
            return None, Refusal(
                f"edge {edge} does not lead {current}->{target}. Allowed here: {names}."
            )
        chosen = matched
    elif len(candidates) > 1:
        names = ", ".join(item.name for item in candidates)
        return None, Refusal(
            f"{current}->{target} has more than one meaning: {names}. Name the edge with "
            "`--edge`; they differ in verdict, proof and what happens next."
        )
    else:
        chosen = candidates[0]

    if not evidence.strip():
        return None, Refusal(
            f"transition {chosen.name} needs --evidence: one line saying what justified "
            "the move, stored in the journal."
        )

    ledger_text = ledger.read_text(encoding="utf-8", errors="replace")
    for problem in ledger_readers.counter_problems(ledger_text):
        return None, Refusal(problem)
    refusal = chosen.guard(ledger_text, state)
    if refusal is not None:
        return None, refusal

    # Invariant 32: one record per event, appended. The reason goes into ``why`` so a
    # reader of the journal sees the same justification the agent wrote.
    _journal.append_event(
        ledger,
        event="advance",
        why=evidence.strip(),
        **{"from": current, "to": target, "edge": chosen.name},
    )
    updated = load(ledger)
    return updated, None


def _require_ledger(raw: str) -> Path:
    """Validate the ledger path argument.

    Args:
        raw: Path from argv.

    Returns:
        Existing ledger path.

    Raises:
        SystemExit: If the ledger does not exist.
    """
    ledger = Path(raw).expanduser()
    if not ledger.is_file():
        print(f"bugfixer-state: no such ledger: {ledger}", file=sys.stderr)
        raise SystemExit(2)
    return ledger


def _set_status(ledger: Path, raw: str) -> int:
    """Record a run status in the journal.

    Terminal statuses are refused while the task still holds a secret: the finish is the statement
    "this is done", and a leak beside it is an unfinished job (invariant 26, §10). Ordinary work
    stays open — the operator's ruling of 2026-11-28 15:02 is that writing a secret is refused,
    not access to a file that holds one — but the run cannot be declared clean.

    Args:
        ledger: Ledger file.
        raw: Status value from argv.

    Returns:
        Process exit code.
    """
    status = raw.upper()
    if status not in VALID_STATUSES:
        print(f"bugfixer-state: status must be one of {VALID_STATUSES}", file=sys.stderr)
        return 2
    if status in {"DONE", "PARTIAL", "LIMIT_REACHED", "BLOCKED"}:
        text = ledger.read_text(encoding="utf-8", errors="replace")
        lingering = ledger_readers.secret_problems(ledger, text)
        if lingering:
            print(
                "bugfixer-state: the task still holds a secret, so the run cannot be declared "
                f"finished: {lingering[0]} Remove it first — the status would record a finished "
                "run beside an unresolved leak (spec §10, invariant 26).",
                file=sys.stderr,
            )
            return 2
    _journal.append_event(
        ledger, event="status", why="status recorded by the state CLI", status=status
    )
    print(json.dumps(load(ledger), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: ``0`` success, ``2`` refused or invalid input.
    """
    parser = argparse.ArgumentParser(
        prog="bugfixer_gate_state.py",
        description="Read or advance the phase state machine for a bug-fixer ledger.",
    )
    parser.add_argument("ledger", help="ledger file (the task file for the bug)")
    parser.add_argument("field", nargs="?", choices=("phase", "advance", "status", "edges"))
    parser.add_argument("value", nargs="?", help="target phase, or status value")
    parser.add_argument(
        "--evidence",
        default="",
        help="what justified this transition; stored verbatim in history",
    )
    parser.add_argument(
        "--edge",
        default=None,
        help="edge name; required when several edges share the phase pair",
    )
    args = parser.parse_args(argv)

    ledger = _require_ledger(args.ledger)

    if args.field is None:
        print(json.dumps(load(ledger), indent=2))
        return 0

    if args.field == "edges":
        for item in EDGES:
            print(f"{item.name}\t{item.from_phase}->{item.to_phase}")
        return 0

    if args.value is None:
        print(f"bugfixer-state: {args.field} requires a value", file=sys.stderr)
        return 2

    if args.field == "status":
        return _set_status(ledger, args.value)

    try:
        target = int(args.value)
    except ValueError:
        print(f"bugfixer-state: phase must be one of {VALID_PHASES}", file=sys.stderr)
        return 2

    state, refusal = advance(ledger, target, evidence=args.evidence, edge=args.edge)
    if refusal is not None:
        print(f"bugfixer-state: {refusal.reason}", file=sys.stderr)
        return 2
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
