#!/usr/bin/env python3
"""The run journal: how a bug-fixer run reached its current state (spec §5.4).

The journal is the third artifact of §5.3, and the only one the agent does not write.
Two rules make it worth having:

* **append-only** — one JSON object per line, never rewritten (invariant 32);
* **written by the runtime** — the state CLI appends, the gate refuses an agent's write
  (invariant 33), so the phase cannot be chosen by editing a number.

Because the phase, the status and the active hypothesis are read from the last matching
record (invariant 34), there is no second state file: nothing to drift, nothing to forge.
A missing journal is not a refusal — it means the run has not started (invariant 36).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

#: Suffix of the journal, appended to the task file's own name.
JOURNAL_SUFFIX: Final[str] = ".bugfixer.jsonl"

#: Event kinds §5.4 allows. A typo would otherwise sit in the file unread and unreadable.
EVENT_KINDS: Final[frozenset[str]] = frozenset(
    {"start", "advance", "deny", "verdict", "status", "end"}
)

#: Fields every record carries.
REQUIRED_FIELDS: Final[tuple[str, ...]] = ("at", "event", "why")


class JournalError(Exception):
    """A journal that cannot be read.

    Raised rather than swallowed: a corrupt record means the state is unknown, and
    guessing "phase 1" would silently reopen a finished run's write ban.
    """


def journal_path(ledger: Path) -> Path:
    """Return the journal path for a task file.

    Args:
        ledger: Task file the run works on.

    Returns:
        Path of ``<task file>.bugfixer.jsonl``, beside the task file.
    """
    return ledger.with_name(ledger.name + JOURNAL_SUFFIX)


def _now() -> str:
    """ISO 8601 UTC timestamp, second resolution."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(ledger: Path, *, event: str, why: str, **fields: Any) -> dict[str, Any]:
    """Append one record to the journal.

    Args:
        ledger: Task file the run works on.
        event: One of :data:`EVENT_KINDS`.
        why: One-line reason: what allowed a transition, what was refused, how a probe
            ended. Line breaks are folded, because one record is one line.
        **fields: Record-specific fields from §5.4's table (``from``, ``to``, ``edge``,
            ``hypothesis``, ``result``, ``status``, ``tool``, …).

    Returns:
        The record as written.

    Raises:
        ValueError: When the event kind is not one of the six §5.4 names.
    """
    if event not in EVENT_KINDS:
        raise ValueError(f"unknown journal event {event!r}; allowed: {sorted(EVENT_KINDS)}")
    record: dict[str, Any] = {"at": _now(), "event": event, "why": " ".join(why.split())}
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    path = journal_path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(ledger: Path) -> list[dict[str, Any]]:
    """Read every record, oldest first.

    Args:
        ledger: Task file the run works on.

    Returns:
        Records in file order; empty when the journal does not exist.

    Raises:
        JournalError: When a line is not a JSON object, or misses a required field.
    """
    path = journal_path(ledger)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JournalError(f"cannot read {path}: {exc}") from exc
    events: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalError(f"{path} line {number} is not JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise JournalError(f"{path} line {number} is a {type(record).__name__}, not an object")
        missing = [field for field in REQUIRED_FIELDS if not record.get(field)]
        if missing:
            raise JournalError(f"{path} line {number} misses {', '.join(missing)}")
        events.append(record)
    return events


def last_of(events: list[dict[str, Any]], *kinds: str) -> dict[str, Any] | None:
    """The newest record whose ``event`` is one of ``kinds``.

    Args:
        events: Records in file order.
        *kinds: Event names to accept.

    Returns:
        The newest matching record, or ``None``.
    """
    for record in reversed(events):
        if record.get("event") in kinds:
            return record
    return None


def state_of(ledger: Path) -> dict[str, Any]:
    """Derive the run state from the journal (invariant 34).

    Args:
        ledger: Task file the run works on.

    Returns:
        Mapping with ``phase``, ``status``, ``hypothesis`` and ``transitions``. Absent
        journal means an unstarted run: phase 1, status RUNNING, no history.

    Raises:
        JournalError: When the journal cannot be read or its phase is not 1–5.
    """
    events = read_events(ledger)
    phase = 1
    status = "RUNNING"
    hypothesis = ""
    transitions: list[dict[str, Any]] = []
    for record in events:
        kind = record.get("event")
        if kind == "advance":
            target = record.get("to")
            if target not in (1, 2, 3, 4, 5):
                raise JournalError(
                    f"{journal_path(ledger)} records an advance to phase {target!r}, "
                    "which is not one of 1-5"
                )
            phase = int(target)
            transitions.append(record)
            if record.get("hypothesis"):
                hypothesis = str(record["hypothesis"])
        elif kind in {"status", "end"}:
            if record.get("status"):
                status = str(record["status"])
        elif kind == "verdict":
            if record.get("hypothesis"):
                hypothesis = str(record["hypothesis"])
    return {"phase": phase, "status": status, "hypothesis": hypothesis, "transitions": transitions}


def main() -> int:
    """Print a ledger's journal, one record per line.

    Returns:
        ``0`` on success, ``2`` when the journal cannot be read.
    """
    if len(sys.argv) != 2:
        print("usage: bugfixer_journal.py <task file>", file=sys.stderr)
        return 2
    ledger = Path(sys.argv[1]).expanduser()
    try:
        events = read_events(ledger)
    except JournalError as exc:
        print(f"bugfixer-journal: {exc}", file=sys.stderr)
        return 2
    for record in events:
        print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
