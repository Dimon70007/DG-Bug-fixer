"""Clean measurement of the contract search depth through real payload shapes.

Supersedes probe_b5_shell_deep.py, whose verdict helper crashed on relative_to() and reported
the helper's ValueError as if it were the gate's answer.
"""

from __future__ import annotations

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


def verdict(event: dict, tmp: Path) -> str:
    try:
        root = gate._project_root(event)
    except Exception as exc:  # noqa: BLE001
        return f"root raised {type(exc).__name__}"
    try:
        found = gate.discover_ledger(root)
        return f"OK ledger={found.resolve().relative_to(tmp.resolve())}"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL {type(exc).__name__}"


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        project = tmp / "project"
        (project / "tasks").mkdir(parents=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")

        print("== B-5: contract at the project root, Shell run from a subdirectory ==")
        print("   payload shape: Shell carries a real cwd; Write/Grep carry cwd=null\n")
        chain = project
        for name in ("src", "pkg", "sub", "inner", "leaf"):
            chain = chain / name
            chain.mkdir()
            depth = len(chain.relative_to(project).parts)
            base = {"tool_name": "Shell", "tool_input": {"command": "pytest -q"}}
            deep_cwd = dict(base, cwd=str(chain), workspace_roots=[str(chain)])
            empty_cwd = dict(base, cwd="", workspace_roots=[str(project)])
            print(
                f"  depth {depth}: cwd=subdir -> {verdict(deep_cwd, tmp):<44} "
                f"cwd='' -> {verdict(empty_cwd, tmp)}"
            )


if __name__ == "__main__":
    main()
