"""ST-9 probe 17: F13 and F14 re-measured on a NEUTRAL stand.

Probe 16 put the scratch project under `skills/bug-fixer/logs/dod14/`, which is itself both a
plugin path and a `logs` path — so both answers it gave were about the scratch location, not
about the rule. This probe uses `tempfile.mkdtemp()` with no `dir=`, i.e. the OS temp dir,
which is the honest stand for a project that is not the plugin.

F12 (quoting) does not depend on where the project lives and stays as measured.
"""

from __future__ import annotations

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
    parent = Path(tempfile.mkdtemp())
    print(f"stand: {parent} (outside the plugin tree: {'skills/bug-fixer' not in str(parent)})")
    try:
        project = make_project(parent)
        ledger = project / "tasks" / "TASK-700.md"
        root = gate._project_of(ledger)
        src = project / "src" / "service.py"
        under_logs = project / "logs" / "gen" / "thing.py"
        under_logs.parent.mkdir(parents=True, exist_ok=True)
        under_logs.write_text("y = 1\n", encoding="utf-8")

        print("\n== F13: does a `.py` under the project's own logs/ count as code? ==")
        for path in (src, under_logs):
            print(
                f"  {str(path.relative_to(project)):22} "
                f"raw_dump={gate._is_raw_dump_write(str(path), ledger, 'write')!s:5} "
                f"doc={gate._is_doc_write(str(path), ledger, root, 'write')!s:5} "
                f"code_write={gate._is_code_write(str(path), ledger, 'write', dev_plugin=False)}"
            )
        print("  invariant 45 says `logs/**` is always writable; a .py there is still a .py")

        print("\n== F14: unresolvable ledger + a write target ==")
        second = project / "tasks" / "TASK-701.md"
        second.write_text("## Task\nb\n", encoding="utf-8")
        stamp = time.time()
        for path in (ledger, second):
            os.utime(path, (stamp, stamp))
        print(f"  tie between: {sorted(p.name for p in (project / 'tasks').glob('TASK-*.md'))}")
        for path in (src, SKILL / "hooks" / "bugfixer_gate.py", project / "bug-fixer.project.md"):
            print(f"  _is_setup_target({str(path)[:52]:54}) -> {gate._is_setup_target(str(path), root)}")

        print("\n  consequence, read from main():")
        print("    LedgerNotFoundError -> _unblocks_setup(tool, args, root)")
        print("    -> allow if EVERY target is the contract or a path containing skills/…/bug-fixer")
        print(f"    so a tie refuses {src.name}: {not gate._is_setup_target(str(src), root)}")
        print(f"    and allows the plugin tree: {gate._is_setup_target(str(SKILL / 'hooks' / 'bugfixer_gate.py'), root)}")
    finally:
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    main()
