"""Is the home-contract scenario reachable from the shipped code, or is it my own stand?

Two claims to test:

1. Does any install path write a contract into `$HOME`? (`_seed_contract` is project-scope only.)
2. Is the 4-level contract search depth reachable in a real project layout — i.e. does an agent
   working deep inside its own project lose the contract that sits at the project root?
"""

from __future__ import annotations

import subprocess
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


def measure_who_writes_contracts() -> None:
    print("== B-5: does anything put a contract in $HOME? ==")
    out = subprocess.run(
        ["rg", "-n", "seed_contract|write_created|scaffold\\(", str(SKILL / "install.py")],
        text=True, capture_output=True)
    for line in out.stdout.strip().splitlines():
        print(f"  install.py: {line.split(':', 2)[-1][:110]}")
    out = subprocess.run(
        ["rg", "-n", "-B", "3", "-A", "6", "def _seed_contract", str(SKILL / "install.py")],
        text=True, capture_output=True)
    body = out.stdout
    print("  plan.project is None for home targets ->",
          "seed skipped" if "plan.project / CONTRACT_NAME" in body else "unclear")
    out = subprocess.run(
        ["rg", "-n", "plan.project", str(SKILL / "install.py")], text=True, capture_output=True)
    for line in out.stdout.strip().splitlines():
        print(f"  {line.split(':', 2)[-1][:110]}")


def measure_deep_project(tmp: Path) -> None:
    print("\n== B-5: agent working deep inside ITS OWN project (contract at the root) ==")
    project = tmp / "project"
    (project / "tasks").mkdir(parents=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")

    deep = project
    for name in ("a", "b", "c", "d", "e"):
        deep = deep / name
        deep.mkdir()
        found = gate._find_contract(deep)
        rel = len(deep.relative_to(project).parts)
        print(f"  cwd {rel} level(s) below the project root: {'found' if found else 'MISS'}")


def measure_deep_shell_event(tmp: Path) -> None:
    print("\n== B-5: same, through the gate's own event path ==")
    project = tmp / "project2"
    deep = project / "src" / "pkg" / "sub" / "inner" / "leaf"
    (project / "tasks").mkdir(parents=True)
    deep.mkdir(parents=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(deep / "mod.py"), "content": "x\n"},
        "cwd": None,
        "workspace_roots": [str(deep)],
    }
    root = gate._project_root(event)
    print(f"  _project_root -> {root}")
    try:
        found = gate.discover_ledger(root)
        print(f"  discover_ledger -> {found}")
    except Exception as exc:  # noqa: BLE001
        print(f"  discover_ledger -> {type(exc).__name__}: {str(exc)[:80]}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        measure_who_writes_contracts()
        measure_deep_project(tmp)
        measure_deep_shell_event(tmp)


if __name__ == "__main__":
    main()
