"""ST-9 probe 20: does the journal-based pin survive the event that broke mtime selection?

F9's mechanism: a new `tasks/TASK-*.md` becomes the newest by mtime and takes the run.
This probe creates exactly that file on a neutral stand and asks three rules what they pick.

Rule A  — newest task-file mtime (today).
Rule B  — newest journal mtime, among candidates that have a journal.
Rule C  — the target named by the call.
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
    raise SystemExit("no Write payload")


def run_hook(project: Path, target: Path) -> tuple[int, str]:
    event = corpus_write()
    event["tool_input"] = {"file_path": str(target), "content": "y\n"}
    event["cwd"] = str(project)
    result = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.returncode, payload.get("permission", "?")


def rules(project: Path) -> dict[str, str]:
    files = sorted((project / "tasks").glob("TASK-*.md"))
    by_mtime = max(files, key=lambda p: p.stat().st_mtime).name
    journalled = [p for p in files if Path(str(p) + ".bugfixer.jsonl").is_file()]
    if journalled:
        by_journal = max(journalled, key=lambda p: Path(str(p) + ".bugfixer.jsonl").stat().st_mtime).name
    else:
        by_journal = "— no journal yet, rule A applies —"
    return {"A: newest task mtime": by_mtime, "B: newest journal mtime": by_journal}


def main() -> None:
    parent = Path(tempfile.mkdtemp())
    try:
        project = parent / "proj"
        for sub in ("tasks", "tests", "src", "logs/dumps"):
            (project / sub).mkdir(parents=True, exist_ok=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
        active = project / "tasks" / "TASK-700.md"
        active.write_text("## Task\nactive run\n", encoding="utf-8")

        print("== start the run: one denied write creates the journal ==")
        code, permission = run_hook(project, project / "src" / "service.py")
        print(f"  deny produced: exit={code} permission={permission}")
        print(f"  journal exists: {Path(str(active) + '.bugfixer.jsonl').is_file()}")
        print(f"  rules: {rules(project)}")

        print("\n== the F9 event: a new task file is created (as TASK-086 was) ==")
        fresh = project / "tasks" / "TASK-701.md"
        fresh.write_text("## Task\nnew task, not started\n", encoding="utf-8")
        time.sleep(0.01)
        os.utime(fresh, None)
        print(f"  rules after creation: {rules(project)}")
        journal = Path(str(active) + ".bugfixer.jsonl")
        before = len(journal.read_text(encoding="utf-8").splitlines())
        code, permission = run_hook(project, project / "src" / "service.py")
        print(f"  next denial: exit={code} permission={permission}")
        after_active = len(journal.read_text(encoding="utf-8").splitlines())
        fresh_journal = Path(str(fresh) + ".bugfixer.jsonl")
        print(f"  records: active {before} -> {after_active}; new-task journal exists: {fresh_journal.is_file()}")
        print(f"  -> today the refusal landed in: {'the NEW task' if fresh_journal.is_file() else 'the active task'}")
    finally:
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    main()
