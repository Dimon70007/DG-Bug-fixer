"""ST-9 probe 13: why two end-to-end results looked impossible.

Probe 12 reported (a) a phase-1 production write allowed and (b) an mtime tie allowed
instead of refused. Both are suspect: (a) the scratch project sat inside the plugin tree,
where `_is_plugin_file` exempts every path; (b) needs the resolver's own answer, not the
subprocess's silence.

This probe drives the resolver and the classifier directly, on the same two shapes.
"""

from __future__ import annotations

import json
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


def main() -> None:
    print("== why (a) was allowed: the scratch path itself ==")
    inside = SKILL / "logs" / "dod14" / "scratch-proj"
    (inside / "tasks").mkdir(parents=True, exist_ok=True)
    (inside / "bug-fixer.project.md").write_text("Task/bug file: tasks/TASK-*.md\n", encoding="utf-8")
    target = inside / "src" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")
    ledger = inside / "tasks" / "TASK-700.md"
    ledger.write_text("## Task\nx\n", encoding="utf-8")

    print(f"  target: {target}")
    print(f"  contains 'skills/bug-fixer'? {'skills/bug-fixer' in str(target)}")
    print(f"  _project_of(ledger) -> {gate._project_of(ledger)}")
    print(f"  _is_plugin_file(target) -> {gate._is_plugin_file(str(target), gate._project_of(ledger), dev_plugin=False)}")
    print(f"  _is_code_write(target)  -> {gate._is_code_write(str(target), ledger, 'write', dev_plugin=False)}")

    print("\n== the same file, described as if the project were elsewhere ==")
    other = Path("/outside-a/proj/src/service.py")
    print(f"  _is_plugin_file({other}) -> {gate._is_plugin_file(str(other), Path('/outside-a/proj'), dev_plugin=False)}")

    print("\n== why (b) may have been silent: the resolver's own answer ==")
    twin_a = inside / "tasks" / "TASK-701.md"
    twin_b = inside / "tasks" / "TASK-702.md"
    twin_a.write_text("## Task\na\n", encoding="utf-8")
    twin_b.write_text("## Task\nb\n", encoding="utf-8")
    stamp = time.time()
    for path in (ledger, twin_a, twin_b):
        __import__("os").utime(path, (stamp, stamp))
    try:
        print(f"  discover_ledger -> {gate.discover_ledger(inside)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  discover_ledger raised {type(exc).__name__}: {str(exc)[:120]}")

    print("\n== and the payload the probe sent ==")
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(inside / "src" / "service.py"), "content": "y\n"},
        "cwd": str(inside),
        "workspace_roots": None,
    }
    print(f"  top-level keys: {sorted(payload)}")
    print(f"  _resolve_ledger(...) -> {gate._resolve_ledger(payload, None)}")

    shutil.rmtree(inside, ignore_errors=True)


if __name__ == "__main__":
    main()
