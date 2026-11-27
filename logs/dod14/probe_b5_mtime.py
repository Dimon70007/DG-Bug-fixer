"""Is the wrong-ledger pick caused by mtime, or by searching the contract's directory at all?

Both files match the same glob, and `discover_ledger` globs `root` *and* `contract.parent`.
If the pick follows mtime, then a home-level contract lets any newer file in the home
directory win — including another project's ledger.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def pick(tmp: Path, home_newer: bool) -> str:
    home = tmp / "home"
    project = home / "github" / "my" / "repo"
    (project / "tasks").mkdir(parents=True, exist_ok=True)
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    (home / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")

    mine = project / "tasks" / "TASK-1.md"
    theirs = home / "tasks" / "TASK-999.md"
    mine.write_text("STATUS: RUNNING\n", encoding="utf-8")
    theirs.write_text("STATUS: RUNNING\n", encoding="utf-8")

    # Force a decisive mtime gap in one direction or the other.
    if home_newer:
        os.utime(mine, (1_600_000_000, 1_600_000_000))
        os.utime(theirs, (1_700_000_000, 1_700_000_000))
    else:
        os.utime(theirs, (1_600_000_000, 1_600_000_000))
        os.utime(mine, (1_700_000_000, 1_700_000_000))

    try:
        found = gate.discover_ledger(project)
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:80]}"
    return rel(found, tmp)


def main() -> None:
    print("== B-5: which ledger does a home-level contract attach, by mtime? ==")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        print(f"  home ledger newer : {pick(tmp, home_newer=True)}")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        print(f"  project ledger newer: {pick(tmp, home_newer=False)}")
    print("\n  A correct resolution must pick the project's own ledger in both cases.")


if __name__ == "__main__":
    main()
