"""ST-9 probe 18: a refusal that cannot be attributed to a ledger is not recorded anywhere.

`main()` calls `_fail_closed` when the ledger cannot be resolved, and `_fail_closed` runs before
any journal path is known. So the one refusal an operator most needs — "the gate cannot tell
which bug this is" — leaves no trace at all. Measured here on a neutral stand.
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


def main() -> None:
    parent = Path(tempfile.mkdtemp())
    try:
        project = parent / "proj"
        for sub in ("tasks", "tests", "src", "logs/dumps"):
            (project / sub).mkdir(parents=True, exist_ok=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
        first = project / "tasks" / "TASK-700.md"
        second = project / "tasks" / "TASK-701.md"
        first.write_text("## Task\na\n", encoding="utf-8")
        second.write_text("## Task\nb\n", encoding="utf-8")
        stamp = time.time()
        for path in (first, second):
            os.utime(path, (stamp, stamp))

        event = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(project / "src" / "service.py"), "content": "y\n"},
            "cwd": str(project),
            "workspace_roots": [str(project)],
        }
        result = subprocess.run(
            [sys.executable, str(GATE)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            cwd=str(project),
            check=False,
        )
        print(f"exit={result.returncode}")
        print(f"message starts: {json.loads(result.stdout).get('agent_message', '')[:80]}…")
        found = sorted(str(p.relative_to(project)) for p in project.rglob("*.jsonl"))
        print(f"journal files anywhere under the project: {found or 'NONE'}")
        print(f"any file created in tasks/: {sorted(p.name for p in (project / 'tasks').iterdir())}")
        print(f"files in the OS temp dir matching *.jsonl: {sorted(p.name for p in parent.glob('**/*.jsonl')) or 'NONE'}")
    finally:
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    main()
