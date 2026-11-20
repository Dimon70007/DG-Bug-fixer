#!/usr/bin/env python3
"""Phase state machine for a bug-fixer ledger (TASK-079.1).

The ledger records findings; it cannot say which phase the run is in, and a hook
cannot infer it. This CLI maintains the small sidecar the gate reads, and owns
the *transitions* between phases:

    bugfixer_gate_state.py <ledger>                       # print state
    bugfixer_gate_state.py <ledger> advance 2 --evidence "BASELINE: 1652/1654"
    bugfixer_gate_state.py <ledger> phase 2               # alias for advance
    bugfixer_gate_state.py <ledger> status PARTIAL

A phase is not a free variable. Every edge in :data:`TRANSITIONS` has a
precondition read from the ledger, so the agent cannot skip Phase 2 by declaring
itself to be in Phase 3. Illegal edges are refused and the refusal names the
missing precondition, because "illegal transition" alone tells an agent nothing
about what to do next.

Markers are anchored at the start of a line. A ledger that *describes* a marker
must not satisfy it: the same defect that made a prose mention of
``STATUS: BLOCKED`` block a finished task.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple

STATE_SUFFIX: Final[str] = ".bugfixer.json"
VALID_PHASES: Final[tuple[int, ...]] = (1, 2, 3)
VALID_STATUSES: Final[tuple[str, ...]] = ("RUNNING", "BLOCKED", "DONE", "PARTIAL")

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
_BASELINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t>*_-]{0,8}BASELINE:", re.MULTILINE
)

#: A hypothesis was proven. Matches the finding-block template's verdict line and
#: the plain form, both anchored.
_CONFIRMED_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t>*_-]{0,8}(?:\*\*Verdict\.\*\*|VERDICT:)[^\n]*\bCONFIRMED\b",
    re.MULTILINE,
)

#: What a loop-back learned. Required when returning from Phase 3.
_NEW_INFO_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t>*_-]{0,8}NEW INFORMATION:", re.MULTILINE
)

#: The PARTIAL remainder list.
_REMAINING_RE: Final[re.Pattern[str]] = re.compile(
    r"^##[ \t]+Remaining[ \t]*$", re.MULTILINE
)

#: A bullet inside the Remaining section.
_REMAINING_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"^[ \t]*[-*][ \t]+\S", re.MULTILINE)


class Refusal(NamedTuple):
    """A refused transition, with the reason the agent needs to act on."""

    reason: str


def state_path(ledger: Path) -> Path:
    """Return the sidecar path for a ledger.

    Args:
        ledger: Ledger file.

    Returns:
        Sidecar path next to the ledger.
    """
    return ledger.with_name(ledger.name + STATE_SUFFIX)


def load(ledger: Path) -> dict[str, Any]:
    """Load the sidecar, defaulting to Phase 1 when absent.

    Args:
        ledger: Ledger file.

    Returns:
        State mapping with ``phase``, ``status``, and ``transitions``.

    Raises:
        SystemExit: If the sidecar exists but is not a JSON object.
    """
    path = state_path(ledger)
    if not path.is_file():
        return {"phase": 1, "status": "RUNNING", "transitions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"bugfixer-state: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(payload, dict):
        print(f"bugfixer-state: {path} is not a JSON object", file=sys.stderr)
        raise SystemExit(2)
    payload.setdefault("transitions", [])
    return payload


def save(ledger: Path, state: dict[str, Any]) -> None:
    """Write the sidecar.

    Args:
        ledger: Ledger file.
        state: State mapping to persist.
    """
    state = dict(state)
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path(ledger).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def count_markers(pattern: re.Pattern[str], ledger_text: str) -> int:
    """Count anchored occurrences of a marker.

    Args:
        pattern: Anchored marker pattern.
        ledger_text: Full ledger contents.

    Returns:
        Number of matches.
    """
    return len(pattern.findall(ledger_text))


def _last_loopback_new_info(state: dict[str, Any]) -> int:
    """How many new-information markers the last loop-back already consumed.

    Args:
        state: Sidecar mapping.

    Returns:
        The recorded count, or ``0`` when no loop-back has happened.
    """
    for entry in reversed(state.get("transitions") or []):
        if isinstance(entry, dict) and entry.get("from") == 3 and entry.get("to") == 1:
            return int(entry.get("new_information_count") or 0)
    return 0


def _require(condition: bool, reason: str) -> Refusal | None:
    """Return a refusal when ``condition`` is false.

    Args:
        condition: Precondition result.
        reason: What is missing, worded as an instruction.

    Returns:
        A :class:`Refusal`, or ``None`` when the precondition holds.
    """
    return None if condition else Refusal(reason)


def guard_1_to_2(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """Entering Phase 2 requires a reproduction and a baseline.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused for this edge).

    Returns:
        A refusal naming the missing marker, or ``None``.
    """
    del state
    if count_markers(_RED_TEST_RE, ledger_text) == 0:
        return Refusal(
            "cannot enter Phase 2: the ledger has no failing test. Write the RED test, "
            "run it, and record it on a line starting `FAILING TEST:` or `RED TEST:`, "
            "with the command and the observed failure."
        )
    if count_markers(_BASELINE_RE, ledger_text) == 0:
        return Refusal(
            "cannot enter Phase 2: the ledger has no baseline. Run the full suite before "
            "any change and record it on a line starting `BASELINE:`, listing what was "
            "already failing so Phase 3 can tell a regression from an old defect."
        )
    return None


def guard_2_to_3(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """Entering Phase 3 requires a confirmed hypothesis.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused for this edge).

    Returns:
        A refusal naming the missing marker, or ``None``.
    """
    del state
    if count_markers(_CONFIRMED_RE, ledger_text) == 0:
        return Refusal(
            "cannot enter Phase 3: no hypothesis is confirmed by experiment. A finding "
            "must carry a verdict line containing `CONFIRMED` (the `**Verdict.**` line of "
            "an `F<n>` block). A fix without a confirmed cause is a guess."
        )
    return None


def guard_3_to_2(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """PARTIAL: Phase 3 returns to Phase 2 only with a listed remainder.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping (unused for this edge).

    Returns:
        A refusal naming what is missing, or ``None``.
    """
    del state
    heading = _REMAINING_RE.search(ledger_text)
    if heading is None:
        return Refusal(
            "cannot return to Phase 2 as PARTIAL: the ledger has no `## Remaining` "
            "section. List what is left, why it is not done, how it is verified, and who "
            "closes it."
        )
    tail = ledger_text[heading.end():]
    if _REMAINING_ITEM_RE.search(tail) is None:
        return Refusal(
            "cannot return to Phase 2 as PARTIAL: `## Remaining` exists but lists nothing. "
            "Add at least one item; an empty remainder means the work is DONE."
        )
    return None


def guard_3_to_1(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """A loop-back from Phase 3 must bring new information.

    Args:
        ledger_text: Full ledger contents.
        state: Sidecar mapping, used to see what a previous loop-back consumed.

    Returns:
        A refusal naming what is missing, or ``None``.
    """
    current = count_markers(_NEW_INFO_RE, ledger_text)
    consumed = _last_loopback_new_info(state)
    if current <= consumed:
        return Refusal(
            "cannot loop back to Phase 1: no new information since the previous pass. "
            "Record what this attempt learned on a line starting `NEW INFORMATION:` "
            "before returning; a return that teaches nothing is a loop, not progress."
        )
    return None


def guard_2_to_1(ledger_text: str, state: dict[str, Any]) -> Refusal | None:
    """Returning to Phase 1 from Phase 2 is always allowed.

    A refuted hypothesis is normal progress, not a failure, so this edge has no
    precondition beyond the ledger existing.

    Args:
        ledger_text: Full ledger contents (unused).
        state: Sidecar mapping (unused).

    Returns:
        Always ``None``.
    """
    del ledger_text, state
    return None


#: Allowed edges. Anything absent from this mapping is illegal by default.
TRANSITIONS: Final[dict[tuple[int, int], Callable[[str, dict[str, Any]], Refusal | None]]] = {
    (1, 2): guard_1_to_2,
    (2, 1): guard_2_to_1,
    (2, 3): guard_2_to_3,
    (3, 1): guard_3_to_1,
    (3, 2): guard_3_to_2,
}


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
) -> tuple[dict[str, Any] | None, Refusal | None]:
    """Move the ledger to ``target`` phase if the edge is legal and proven.

    Args:
        ledger: Ledger file.
        target: Destination phase.
        evidence: Why the transition is justified; stored in history verbatim.

    Returns:
        ``(state, None)`` on success, or ``(None, refusal)`` when refused.
    """
    state = load(ledger)
    current = int(state.get("phase") or 1)

    if target == current:
        return state, None

    guard = TRANSITIONS.get((current, target))
    if guard is None:
        allowed = ", ".join(f"{a}->{b}" for a, b in sorted(TRANSITIONS))
        return None, Refusal(
            f"illegal transition {current}->{target}. Allowed edges: {allowed}. "
            "Skipping a phase is the defect this guard exists to prevent."
        )

    if not evidence.strip():
        return None, Refusal(
            f"transition {current}->{target} needs --evidence: one line saying what "
            "justified the move, stored in the sidecar history."
        )

    ledger_text = ledger.read_text(encoding="utf-8", errors="replace")
    refusal = guard(ledger_text, state)
    if refusal is not None:
        return None, refusal

    entry: dict[str, Any] = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from": current,
        "to": target,
        "evidence": evidence.strip(),
    }
    if (current, target) == (3, 1):
        entry["new_information_count"] = count_markers(_NEW_INFO_RE, ledger_text)

    updated = dict(state)
    updated["phase"] = target
    updated["transitions"] = [*(state.get("transitions") or []), entry]
    save(ledger, updated)
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
    """Record a run status in the sidecar.

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
    state = load(ledger)
    state["status"] = status
    save(ledger, state)
    print(json.dumps(state, indent=2))
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
    parser.add_argument(
        "field",
        nargs="?",
        choices=("phase", "advance", "status"),
        help="'advance' or its alias 'phase' moves the automaton; 'status' sets the run status",
    )
    parser.add_argument("value", nargs="?", help="target phase, or status value")
    parser.add_argument(
        "--evidence",
        default="",
        help="what justified this transition; stored verbatim in history",
    )
    args = parser.parse_args(argv)

    ledger = _require_ledger(args.ledger)

    if args.field is None:
        print(json.dumps(load(ledger), indent=2))
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
    if target not in VALID_PHASES:
        print(f"bugfixer-state: phase must be one of {VALID_PHASES}", file=sys.stderr)
        return 2

    state, refusal = advance(ledger, target, evidence=args.evidence)
    if refusal is not None:
        print(f"bugfixer-state: {refusal.reason}", file=sys.stderr)
        return 2
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
