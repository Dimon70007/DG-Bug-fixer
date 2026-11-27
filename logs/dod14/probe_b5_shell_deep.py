"""Is the 4-level limit reachable from a REAL payload shape?

The corpus says: Write/Grep carry `cwd: null` + `workspace_roots`, while Shell carries a real
`cwd` (the shell's working directory). So the only path that can hand the gate a *deep* root is
Shell run from a subdirectory. Test exactly that, and nothing else.
"""

from __future__ import annotations

import json
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


def build(tmp: Path) -> tuple[Path, Path]:
    project = tmp / "project"
    (project / "tasks").mkdir(parents=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")
    deep = project
    for name in ("src", "pkg", "sub", "inner", "leaf"):
        deep = deep / name
        deep.mkdir()
    return project, deep


def verdict(event: dict) -> str:
    try:
        root = gate._project_root(event)
    except Exception as exc:  # noqa: BLE001
        return f"root raised {type(exc).__name__}"
    try:
        found = gate.discover_ledger(root)
        return f"ledger {found.relative_to(root.parent)}  (root={root.name or root})"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}  (root={root})"


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        project, deep = build(tmp)
        levels = len(deep.relative_to(project).parts)
        print(f"== B-5: Shell run {levels} directory levels below the project root ==")
        print("  payload shape taken from tests/fixtures/cursor_payloads.json (Shell)\n")

        for label, cwd, roots in (
            ("cwd=deep, workspace_roots=[project]", str(deep), [str(project)]),
            ("cwd=deep, workspace_roots=[deep]", str(deep), [str(deep)]),
            ("cwd='', workspace_roots=[project]", "", [str(project)]),
            ("cwd=None, workspace_roots=[project]", None, [str(project)]),
        ):
            event = {
                "tool_name": "Shell",
                "tool_input": {"command": "touch x.py", "cwd": cwd},
                "cwd": cwd,
                "workspace_roots": roots,
            }
            print(f"  {label:<38} -> {verdict(event)}")

        print("\n  and at 3 levels (inside the limit), for contrast:")
        three = project / "src" / "pkg" / "sub"
        event = {"tool_name": "Shell", "tool_input": {"command": "touch x.py"},
                 "cwd": str(three), "workspace_roots": [str(three)]}
        print(f"  cwd={three.relative_to(project)} -> {verdict(event)}")


if __name__ == "__main__":
    main()
