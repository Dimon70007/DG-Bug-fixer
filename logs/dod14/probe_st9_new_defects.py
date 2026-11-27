"""ST-9 probe 16: three defects found while measuring, each with its own repro.

F12 — `_WRITE_COMMAND_RE` scans the raw command string, so text inside quotes counts as shell
      syntax. Three of this session's three denials were this (journal of TASK-086,
      22:11:55Z, 22:12:37Z, 22:13:48Z), and none of them writes anything.

F13 — `_is_raw_dump_write` exempts any target with a `logs` path part, so a production `.py`
      under `*/logs/**` is not code. It is why a scratch project inside the plugin tree was
      allowed in probe 12.

F14 — an unresolvable ledger (two candidates, equal mtime) is turned into `allow` when the
      write target sits under `skills/bug-fixer/**`: `main()` catches `LedgerNotFoundError` and
      asks `_unblocks_setup`, whose plugin-tree clause matches the absolute path.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CONTRACT = """Contract version: 1
Contract verified by: agent 2026-11-28 — pytest ran

## Ledger

Task/bug file: tasks/TASK-*.md
Raw dumps: logs/dumps/

## Tests

Full suite: pytest
Test paths: tests/

## Search

Search: none

## Extra prohibitions

- no sudo
"""


def make_project(parent: Path) -> Path:
    project = parent / "proj"
    for sub in ("tasks", "tests", "src", "logs/dumps"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
    (project / "tasks" / "TASK-700.md").write_text("## Task\ncache returns stale value\n", encoding="utf-8")
    return project


def main() -> None:
    print("== F12: a quoted command is still read as shell syntax ==")
    commands = [
        (r"""python -c "print('a -> b')" """.strip(), "arrow inside a string"),
        (r"""grep -rn "chdir\|mkdir|write_text" logs/""", "quoted alternation with |mkdir"),
        (r"""python -c "print('cat >> x <<EOF')" """.strip(), "quoted heredoc"),
        (r"""rg 'x>>y' docs/""", "quoted double redirect"),
        ("pytest -q 2>/dev/null", "the corpus payload (exempt)"),
    ]
    for command, label in commands:
        print(f"  {label:38} write={bool(gate._WRITE_COMMAND_RE.search(command))!s:5} {command}")

    print("\n== F13: any target with a `logs` part is a log destination ==")
    parent = Path(tempfile.mkdtemp(dir=SKILL / "logs" / "dod14"))
    try:
        project = make_project(parent)
        ledger = project / "tasks" / "TASK-700.md"
        target = project / "src" / "service.py"
        root = gate._project_of(ledger)
        print(f"  src/service.py under logs?                 {gate._is_raw_dump_write(str(target), ledger, 'write')}")
        print(f"  (the same file is not a plugin file)       {gate._is_plugin_file(str(target), root, dev_plugin=False)}")
        print(f"  -> _is_code_write                          {gate._is_code_write(str(target), ledger, 'write', dev_plugin=False)}")

        print("\n== F14: unresolvable ledger + plugin-tree target -> allow ==")
        second = project / "tasks" / "TASK-701.md"
        second.write_text("## Task\nb\n", encoding="utf-8")
        stamp = time.time()
        for path in (ledger, second):
            os.utime(path, (stamp, stamp))
        print(f"  tie: {[p.name for p in (project / 'tasks').glob('TASK-*.md')]}")
        print(f"  setup target under the plugin tree? {gate._is_setup_target(str(target), root)}")
        hook = SKILL / "hooks" / "bugfixer_gate.py"
        print(f"  setup target for {hook.name}: {gate._is_setup_target(str(hook), root)}")
        print(f"  setup target for src/service.py: {gate._is_setup_target(str(target), root)}")
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    print("\n== F14, boundary: a resolvable ledger, plugin file, dev_plugin=False ==")
    print("  (phase 1 refuses it; the exception above is about ledger resolution, not the tree)")


if __name__ == "__main__":
    main()
