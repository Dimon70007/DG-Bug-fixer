"""The decisive shape: cwd=subdirectory, workspace_roots=[project root].

If the gate prefers `cwd` over `workspace_roots`, a Shell call run from a deep subdirectory
loses the contract that sits at the project root — and `failClosed` then blocks every call.
This measures that exact payload, plus the corpus shape for contrast.
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


def run(event: dict, tmp: Path) -> tuple[str, str]:
    try:
        root = gate._project_root(event)
    except Exception as exc:  # noqa: BLE001
        return "?", f"{type(exc).__name__}"
    try:
        found = gate.discover_ledger(root)
        return str(root.resolve().relative_to(tmp.resolve())), f"OK {found.name}"
    except Exception as exc:  # noqa: BLE001
        return str(root.resolve().relative_to(tmp.resolve())), f"FAIL {type(exc).__name__}"


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        project = tmp / "project"
        (project / "tasks").mkdir(parents=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")

        deep = project / "src" / "pkg" / "sub" / "inner"
        deep.mkdir(parents=True)
        rel = deep.relative_to(project)

        shapes = [
            ("corpus shape: cwd='', roots=[project]", "", [str(project)]),
            ("cwd=project, roots=[project]", str(project), [str(project)]),
            (f"cwd={rel}, roots=[project]", str(deep), [str(project)]),
            (f"cwd={rel}, roots=[deep]", str(deep), [str(deep)]),
        ]

        print("== B-5: does _project_root prefer cwd over workspace_roots? ==")
        print(f"   contract and ledger at project/, Shell run 4 levels down ({rel})\n")
        for label, cwd, roots in shapes:
            event = {
                "tool_name": "Shell",
                "tool_input": {"command": "pytest -q", "cwd": cwd},
                "cwd": cwd,
                "workspace_roots": roots,
            }
            root, result = run(event, tmp)
            print(f"  {label:<42} root={root:<24} {result}")

        print("\n  What the gate should do: the project root is in workspace_roots, so the")
        print("  contract is findable. Three of the four shapes agree; one does not.")


if __name__ == "__main__":
    main()
