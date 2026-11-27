"""ST-9 probe 14: reproduce probe 12's two anomalies with the real hook, and print why.

Anomaly (a): a phase-1 production write into `<plugin>/logs/dod14/scratch/proj/src/service.py`
was allowed. Probe 13 showed `_is_code_write` returns False for it, so the path is exempt.
Which exemption, exactly?

Anomaly (b): an mtime tie returned exit 0 instead of a refusal. Probe 13 showed the resolver
raises on a tie when called directly. So the difference must be in what the hook resolved —
this probe prints the subprocess's raw stdout, stderr and the resolved ledger.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
GATE = SKILL / "hooks" / "bugfixer_gate.py"
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
    (project / "tasks").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src").mkdir()
    (project / "logs" / "dumps").mkdir(parents=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
    return project


def hook(project: Path, event: dict) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def write_event(project: Path, target: Path) -> dict:
    return {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "y = 2\n"},
        "cwd": str(project),
        "workspace_roots": [str(project)],
    }


def main() -> None:
    print("== (a) scratch project inside the plugin's logs/ tree ==")
    inside_parent = Path(tempfile.mkdtemp(dir=SKILL / "logs" / "dod14"))
    try:
        project = make_project(inside_parent)
        ledger = project / "tasks" / "TASK-700.md"
        ledger.write_text("## Task\ncache returns stale value\n", encoding="utf-8")
        target = project / "src" / "service.py"
        root = gate._project_of(ledger)
        print(f"  project        : {project}")
        print(f"  _project_of    : {root}")
        print(f"  _is_plugin_file: {gate._is_plugin_file(str(target), root, dev_plugin=False)}")
        print(f"  _is_raw_dump   : {gate._is_raw_dump_write(str(target), ledger, 'write')}")
        print(f"  _is_doc_write  : {gate._is_doc_write(str(target), ledger, root, 'write')}")
        print(f"  raw_dump_paths : {gate._raw_dump_paths(ledger)}")
        code, stdout, stderr = hook(project, write_event(project, target))
        print(f"  hook exit={code} stdout={stdout}")
        if stderr:
            print(f"  stderr={stderr[:300]}")
    finally:
        shutil.rmtree(inside_parent, ignore_errors=True)

    print("\n== the same shape outside the plugin tree (TMPDIR) ==")
    outside_parent = Path(tempfile.mkdtemp())
    try:
        project = make_project(outside_parent)
        ledger = project / "tasks" / "TASK-700.md"
        ledger.write_text("## Task\ncache returns stale value\n", encoding="utf-8")
        target = project / "src" / "service.py"
        code, stdout, stderr = hook(project, write_event(project, target))
        print(f"  hook exit={code} stdout={stdout[:200]}")
        journal = Path(str(ledger) + ".bugfixer.jsonl")
        print(f"  journal exists: {journal.is_file()}")
        if journal.is_file():
            for line in journal.read_text(encoding="utf-8").splitlines():
                print(f"    {line[:160]}")
    finally:
        shutil.rmtree(outside_parent, ignore_errors=True)

    print("\n== (b) mtime tie through the hook, printing raw output ==")
    tie_parent = Path(tempfile.mkdtemp())
    try:
        project = make_project(tie_parent)
        first = project / "tasks" / "TASK-700.md"
        second = project / "tasks" / "TASK-701.md"
        first.write_text("## Task\na\n", encoding="utf-8")
        second.write_text("## Task\nb\n", encoding="utf-8")
        stamp = time.time()
        for path in (first, second):
            os.utime(path, (stamp, stamp))
        print(f"  mtimes equal: {first.stat().st_mtime == second.stat().st_mtime}")
        code, stdout, stderr = hook(project, write_event(project, project / "src" / "service.py"))
        print(f"  hook exit={code}")
        print(f"  stdout: {stdout[:220]}")
        print(f"  stderr: {stderr[:220]}")
        print(f"  journals under project: {[p.name for p in project.rglob('*.jsonl')]}")
        for journal in project.rglob("*.jsonl"):
            print(f"    {journal.name}: {len(journal.read_text(encoding='utf-8').splitlines())} records")
    finally:
        shutil.rmtree(tie_parent, ignore_errors=True)


if __name__ == "__main__":
    main()
