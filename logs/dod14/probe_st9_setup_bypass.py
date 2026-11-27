"""ST-9 probe 19: is the setup exemption reachable through an unresolvable ledger?

`main()` catches `LedgerNotFoundError` and asks `_unblocks_setup(tool, args, root)`, which allows
a write whose every target is the contract or a path containing `skills/…/bug-fixer`. Probe 17
showed that test is True for the plugin tree when the *project* is elsewhere. If a tie is enough
to reach that branch, then a tie plus a plugin write is an allow — with `--dev-plugin` absent.

Neutral stand, the real hook, the captured corpus's Write payload.
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
CORPUS = SKILL / "tests" / "fixtures" / "cursor_payloads.json"

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


def corpus_write() -> dict:
    for entry in json.loads(CORPUS.read_text(encoding="utf-8")):
        if entry.get("tool_name") == "Write":
            return dict(entry)
    raise SystemExit("no Write payload in the corpus")


def hook(project: Path, target: str, extra: list[str] | None = None) -> tuple[int, str]:
    event = corpus_write()
    event["tool_input"] = {"file_path": target, "content": "y = 2\n"}
    event["cwd"] = str(project)
    result = subprocess.run(
        [sys.executable, str(GATE), *(extra or [])],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.returncode, payload.get("permission", "?") + " " + payload.get("agent_message", "")[:70]


def main() -> None:
    parent = Path(tempfile.mkdtemp())
    try:
        project = parent / "proj"
        for sub in ("tasks", "tests", "src", "logs/dumps"):
            (project / sub).mkdir(parents=True, exist_ok=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        first = project / "tasks" / "TASK-700.md"
        second = project / "tasks" / "TASK-701.md"
        first.write_text("## Task\na\n", encoding="utf-8")
        second.write_text("## Task\nb\n", encoding="utf-8")

        print("== unique ledger: the plugin tree is refused (invariant 47) ==")
        code, note = hook(project, str(SKILL / "hooks" / "bugfixer_gate.py"))
        print(f"  exit={code} {note}")

        print("\n== now make the ledger unresolvable (equal mtime) ==")
        stamp = time.time()
        for path in (first, second):
            os.utime(path, (stamp, stamp))
        code, note = hook(project, str(SKILL / "hooks" / "bugfixer_gate.py"))
        print(f"  exit={code} {note}")
        code, note = hook(project, str(project / "src" / "service.py"))
        print(f"  (control: project source) exit={code} {note}")
        code, note = hook(project, str(project / "bug-fixer.project.md"))
        print(f"  (control: the contract)    exit={code} {note}")
    finally:
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    main()
