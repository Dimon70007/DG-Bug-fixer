#!/usr/bin/env python3
"""Verify that each agent harness actually blocks when the gate denies (TASK-079.2).

A hook template that is valid JSON proves nothing. What matters is whether the
harness refuses a call the gate denies. This script reports, per harness:

* ``verified``        - the harness is installed, wired, and a deny was reproduced
* ``wired``           - wired but the deny could not be reproduced here
* ``not-wired``       - the harness is installed but no hook config was found
* ``not-installed``   - the harness is not on PATH
* ``not-automatable`` - installed, but it needs interactive sign-in to run

The distinction is the point. A missing or unprovable harness must never read as
a pass, or the table becomes a green wall that means nothing.

Exit status: non-zero only when the gate itself fails its self-test. Missing
harnesses are not failures, and the script never claims a deny it did not see.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: This file lives at <repo>/skills/bug-fixer/hooks/verify_harnesses.py.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
GATE: Final[Path] = REPO_ROOT / "skills" / "bug-fixer" / "hooks" / "bugfixer_gate.py"

#: Harness id -> (binary on PATH, project config path this repo would use).
HARNESSES: Final[dict[str, tuple[str, Path]]] = {
    "cursor": ("cursor-agent", REPO_ROOT / ".cursor" / "hooks.json"),
    "claude-code": ("claude", REPO_ROOT / ".claude" / "settings.json"),
    "codex": ("codex", REPO_ROOT / ".codex" / "hooks.json"),
    "opencode": ("opencode", REPO_ROOT / ".opencode" / "plugin" / "bugfixer-gate.ts"),
    "openclaw": ("openclaw", REPO_ROOT / ".openclaw" / "plugins" / "bugfixer-gate.ts"),
}


@dataclass
class Row:
    """One line of the verification matrix."""

    harness: str
    installed: bool
    config: str
    verdict: str
    note: str


def _gate_selftest(tmp: Path) -> tuple[bool, str]:
    """Run the gate against a synthetic deny to prove the gate itself works.

    Args:
        tmp: Scratch directory for the synthetic ledger.

    Returns:
        ``(ok, detail)`` where ``ok`` means a deny was reproduced.
    """
    (tmp / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp / "TASK-000.md"
    ledger.write_text("## Iteration 1\n", encoding="utf-8")

    payload = json.dumps({"tool_name": "write", "tool_input": {"file_path": "src/main.py"}})
    result = subprocess.run(
        [sys.executable, str(GATE), "--ledger", str(ledger)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 2:
        return False, f"expected exit 2, got {result.returncode}"
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "gate printed no JSON"
    if body.get("permission") != "deny":
        return False, f"expected permission deny, got {body.get('permission')!r}"
    return True, "phase-1 src write denied"


def _classify(harness: str, binary: str, config: Path) -> Row:
    """Decide the matrix cell for one harness.

    Args:
        harness: Harness id.
        binary: Executable name to look for on PATH.
        config: Where this repo would place the hook config.

    Returns:
        A populated :class:`Row`.
    """
    installed = shutil.which(binary) is not None
    wired = config.exists()

    if not installed:
        return Row(
            harness,
            False,
            "absent" if not wired else "present",
            "not-installed",
            f"{binary} not on PATH",
        )
    if not wired:
        return Row(harness, True, "absent", "not-wired", "installed, no hook config in this repo")
    return Row(
        harness,
        True,
        "present",
        "not-automatable",
        "installed and wired; a deny needs an authenticated session",
    )


def main() -> int:
    """Print the verification matrix.

    Returns:
        ``2`` when the gate itself fails its self-test, else ``0``.
    """
    with tempfile.TemporaryDirectory() as raw:
        ok, detail = _gate_selftest(Path(raw))

    if not ok:
        print(f"gate self-test: FAIL - {detail}", file=sys.stderr)
        return 2
    print(f"gate self-test: PASS - {detail}\n")

    rows = [_classify(name, binary, config) for name, (binary, config) in HARNESSES.items()]

    width = max(len(row.harness) for row in rows)
    print(f"{'harness'.ljust(width)}  {'installed':9}  {'config':8}  {'verdict':16}  note")
    print(f"{'-' * width}  {'-' * 9}  {'-' * 8}  {'-' * 16}  {'-' * 44}")
    for row in rows:
        print(
            f"{row.harness.ljust(width)}  {str(row.installed).ljust(9)}  "
            f"{row.config.ljust(8)}  {row.verdict.ljust(16)}  {row.note}"
        )

    verified = [row.harness for row in rows if row.verdict == "verified"]
    print()
    print(
        f"verified denies: {len(verified)} of {len(rows)}"
        + (f" ({', '.join(verified)})" if verified else "")
    )
    print("A harness that is not-installed or not-automatable is NOT a pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
