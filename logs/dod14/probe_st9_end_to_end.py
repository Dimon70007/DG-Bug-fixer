"""ST-9 probe 12: the real hook path, driven by the captured payload corpus.

Everything here goes through `bugfixer_gate.py` as a subprocess, with the payloads from
`tests/fixtures/cursor_payloads.json` — the corpus, not an invented shape. Two questions:

1. What does a deny record contain today, end to end?
2. When the ledger cannot be resolved (two candidates with equal mtime), where does the
   refusal go? `_fail_closed` runs before any journal is known.
"""

from __future__ import annotations

import json
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

CONTRACT = """# bug-fixer contract: probe

Contract version: 1
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


def payload(tool: str) -> dict:
    """A corpus entry, with its paths pointed at the probe project."""
    for entry in json.loads(CORPUS.read_text(encoding="utf-8")):
        if entry.get("tool_name") == tool:
            return dict(entry)
    raise SystemExit(f"corpus has no {tool} payload")


def run(project: Path, event: dict) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def make_project(root: Path) -> Path:
    project = root / "proj"
    (project / "tasks").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src").mkdir()
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "logs" / "dumps").mkdir(parents=True)
    return project


def main() -> None:
    scratch = Path(tempfile.mkdtemp(dir=SKILL / "logs" / "dod14"))
    try:
        project = make_project(scratch)
        ledger = project / "tasks" / "TASK-700.md"
        ledger.write_text("## Task\ncache returns stale value\n", encoding="utf-8")
        (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")

        print("== a phase-1 production write, through the hook ==")
        write = payload("Write")
        write["tool_input"] = {"file_path": str(project / "src" / "service.py"), "content": "y = 2\n"}
        write["cwd"] = str(project)
        code, out = run(project, write)
        print(f"  exit={code}")
        print(f"  payload keys: {sorted(json.loads(out))}")
        journal = Path(str(ledger) + ".bugfixer.jsonl")
        records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        print(f"  journal records: {len(records)}")
        print(f"  record fields  : {sorted(records[0])}")
        print(f"  record         : {json.dumps(records[0], ensure_ascii=False)[:200]}…")

        print("\n== a read-only command with a quoted arrow (the 22:11Z refusal) ==")
        shell = payload("Shell")
        shell["tool_input"] = {"command": "python -c \"print('a -> b')\"", "cwd": ""}
        shell["workspace_roots"] = [str(project)]
        code, out = run(project, shell)
        print(f"  exit={code} permission={json.loads(out).get('permission')}")
        print(f"  journalled now : {len(journal.read_text(encoding='utf-8').splitlines())} records")

        print("\n== and with the command the corpus actually captured ==")
        shell_ok = payload("Shell")
        shell_ok["workspace_roots"] = [str(project)]
        code, out = run(project, shell_ok)
        print(f"  `{shell_ok['tool_input']['command']}` -> exit={code} permission={json.loads(out).get('permission')}")

        print("\n== two candidates with equal mtime: where does the refusal go? ==")
        twin_a = project / "tasks" / "TASK-701.md"
        twin_b = project / "tasks" / "TASK-702.md"
        twin_a.write_text("## Task\na\n", encoding="utf-8")
        twin_b.write_text("## Task\nb\n", encoding="utf-8")
        stamp = time.time()
        for path in (twin_a, twin_b, ledger):
            __import__("os").utime(path, (stamp, stamp))
        before = {p.name: p.stat().st_size for p in project.rglob("*.jsonl")}
        code, out = run(project, write)
        after = {p.name: p.stat().st_size for p in project.rglob("*.jsonl")}
        print(f"  exit={code}")
        print(f"  message: {json.loads(out).get('agent_message', '')[:110]}…")
        print(f"  journal sizes before/after: {before} -> {after}")
        print(f"  any journal anywhere under the project: {[p.name for p in project.rglob('*.jsonl')]}")
        print(f"  any journal anywhere under tmp scratch: {[p.name for p in scratch.rglob('*.jsonl')]}")

        print("\n== a shell write to a task file: does it produce a usable target today? ==")
        heredoc = payload("Shell")
        heredoc["tool_input"] = {
            "command": f"cat >> {ledger} <<'EOF'\n### F2 — x\nEOF",
            "cwd": "",
        }
        heredoc["workspace_roots"] = [str(project)]
        code, out = run(project, heredoc)
        print(f"  exit={code} permission={json.loads(out).get('permission')}")
    finally:
        shutil.rmtree(scratch)


if __name__ == "__main__":
    main()
