#!/usr/bin/env python3
"""Phase and status state for a bug-fixer ledger (TASK-079).

The ledger records findings; it cannot say which phase the run is in, and a hook
cannot infer it. This CLI maintains the small sidecar the gate reads:

    bugfixer_gate_state.py <ledger>                # print state
    bugfixer_gate_state.py <ledger> phase 2        # advance to Phase 2
    bugfixer_gate_state.py <ledger> status BLOCKED # record a terminal status

The sidecar only holds what the ledger cannot express. Iteration counts are
always re-derived from the ledger, never trusted from here.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

STATE_SUFFIX: Final[str] = ".bugfixer.json"
VALID_PHASES: Final[tuple[int, ...]] = (1, 2, 3)
VALID_STATUSES: Final[tuple[str, ...]] = ("RUNNING", "BLOCKED", "DONE")


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
        State mapping with ``phase`` and ``status``.

    Raises:
        SystemExit: If the sidecar exists but is not valid JSON.
    """
    path = state_path(ledger)
    if not path.is_file():
        return {"phase": 1, "status": "RUNNING"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"bugfixer-state: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(payload, dict):
        print(f"bugfixer-state: {path} is not a JSON object", file=sys.stderr)
        raise SystemExit(2)
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="bugfixer_gate_state.py",
        description="Read or set the phase/status sidecar for a bug-fixer ledger.",
    )
    parser.add_argument("ledger", help="ledger file (the task file for the bug)")
    parser.add_argument("field", nargs="?", choices=("phase", "status"), help="field to set")
    parser.add_argument("value", nargs="?", help="new value for the field")
    args = parser.parse_args(argv)

    ledger = _require_ledger(args.ledger)
    state = load(ledger)

    if args.field is None:
        print(json.dumps(state, indent=2))
        return 0

    if args.value is None:
        print(f"bugfixer-state: {args.field} requires a value", file=sys.stderr)
        return 2

    if args.field == "phase":
        try:
            phase = int(args.value)
        except ValueError:
            print(f"bugfixer-state: phase must be one of {VALID_PHASES}", file=sys.stderr)
            return 2
        if phase not in VALID_PHASES:
            print(f"bugfixer-state: phase must be one of {VALID_PHASES}", file=sys.stderr)
            return 2
        state["phase"] = phase
    else:
        status = args.value.upper()
        if status not in VALID_STATUSES:
            print(f"bugfixer-state: status must be one of {VALID_STATUSES}", file=sys.stderr)
            return 2
        state["status"] = status

    save(ledger, state)
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
