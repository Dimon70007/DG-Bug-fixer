"""ST-9 probe 15: probe 12 reported `exit=0` for an mtime tie, probe 14 reported `exit=2`
for what looked like the same shape. One of the two is a harness artefact. This probe
reproduces BOTH byte-for-byte, side by side, and prints what each one actually sent.

Difference under test: the corpus Write payload carries `workspace_roots: null` and no
`ledger` key; probe 14 added `workspace_roots: [project]`.
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
    raise SystemExit("corpus has no Write payload")


def make_project(parent: Path) -> Path:
    project = parent / "proj"
    for sub in ("tasks", "tests", "src", "logs/dumps"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
    return project


def tie(project: Path) -> None:
    (project / "tasks" / "TASK-700.md").write_text("## Task\na\n", encoding="utf-8")
    (project / "tasks" / "TASK-701.md").write_text("## Task\nb\n", encoding="utf-8")
    stamp = time.time()
    for path in (project / "tasks").glob("TASK-*.md"):
        os.utime(path, (stamp, stamp))
    stamps = {p.name: p.stat().st_mtime for p in (project / "tasks").glob("TASK-*.md")}
    print(f"  mtimes: {stamps}")
    print(f"  all equal: {len(set(stamps.values())) == 1}")


def hook(project: Path, event: dict, label: str) -> None:
    print(f"\n  [{label}] event keys={sorted(event)}")
    print(f"    tool_input keys={sorted(event.get('tool_input', {}))}")
    result = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )
    print(f"    exit={result.returncode} stdout={result.stdout.strip()[:150]}")
    if result.stderr.strip():
        print(f"    stderr={result.stderr.strip()[:150]}")


def main() -> None:
    for label, parent in (
        ("inside the plugin tree", Path(tempfile.mkdtemp(dir=SKILL / "logs" / "dod14"))),
        ("in TMPDIR", Path(tempfile.mkdtemp())),
    ):
        print(f"\n== {label} ==")
        try:
            project = make_project(parent)
            tie(project)

            corpus = corpus_write()
            corpus["tool_input"] = {"file_path": str(project / "src" / "service.py"), "content": "y = 2\n"}
            corpus["cwd"] = str(project)
            hook(project, corpus, "corpus payload, cwd only")

            explicit = {
                "tool_name": "Write",
                "tool_input": {"file_path": str(project / "src" / "service.py"), "content": "y = 2\n"},
                "cwd": str(project),
                "workspace_roots": [str(project)],
            }
            hook(project, explicit, "explicit payload, cwd + workspace_roots")

            noledger = dict(explicit)
            noledger.pop("cwd")
            hook(project, noledger, "no cwd, workspace_roots only")
        finally:
            shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    main()
