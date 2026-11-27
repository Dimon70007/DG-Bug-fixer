"""What the payload root is used FOR, and whether it is the plugin or the project.

Three call sites consume `_project_root(event)`:
  1. `_resolve_ledger` -> `discover_ledger(root)`: where to look for contract/ledger;
  2. `_unblocks_setup(...)`: which relative targets count as setup work (root / target);
  3. `_contract_placeholders(root)`: which contract to read the fields from.

Every other boundary — which writes are inside the project (`_project_of(ledger)`) and which
are the plugin's own tree (`_is_plugin_file`, path markers) — does not use it at all.
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


def rel(path: Path, base: Path) -> str:
    return str(path.resolve()).replace(str(base.resolve()) + "/", "")


def measure_plugin_vs_project(tmp: Path) -> None:
    project = tmp / "myproject"
    (project / "tasks").mkdir(parents=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")

    installed = project / ".cursor" / "skills" / "bug-fixer"
    (installed / "hooks").mkdir(parents=True)
    (installed / "SKILL.md").write_text("---\nname: bug-fixer\n---\n", encoding="utf-8")
    (project / "src" / "a" / "b" / "c").mkdir(parents=True)

    print("== 1. what the payload root names ==")
    for label, event in (
        ("Write, cwd=project root", {"cwd": str(project), "workspace_roots": [str(project)]}),
        ("Write, cwd=null", {"cwd": None, "workspace_roots": [str(project)]}),
        ("Shell from src/a/b/c", {"cwd": str(project / "src" / "a" / "b" / "c"),
                                  "workspace_roots": [str(project)]}),
        ("hook run from the plugin dir", {"cwd": str(installed), "workspace_roots": [str(installed)]}),
    ):
        root = gate._project_root(event)
        print(f"  {label:<30} -> {rel(root, tmp)}")

    print("\n== 2. the plugin's tree is found by path markers, not by that root ==")
    for tool_path in (
        ".cursor/skills/bug-fixer/hooks/run-gate.sh",
        ".cursor/skills/bug-fixer/lib/bugfixer/contract.py",
        "src/app.py",
    ):
        absolute = str(project / tool_path)
        print(
            f"  {tool_path:<44} plugin_file={gate._is_plugin_file(absolute, project)!s:<5} "
            f"with --dev-plugin={gate._is_plugin_file(absolute, project, dev_plugin=True)}"
        )

    print("\n== 3. the write boundary is the CONTRACT's directory ==")
    ledger = project / "tasks" / "TASK-1.md"
    print(f"  _project_of(ledger) -> {rel(gate._project_of(ledger), tmp)}  (payload root not consulted)")

    print("\n== 4. does a deep Shell cwd change what the gate thinks the project is? ==")
    event = {"cwd": str(project / "src" / "a" / "b" / "c"), "workspace_roots": [str(project)]}
    root = gate._project_root(event)
    print(f"  root = {rel(root, tmp)}")
    print(f"  equals project? {root.resolve() == project.resolve()}")
    print(f"  equals plugin?  {root.resolve() == installed.resolve()}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        measure_plugin_vs_project(Path(raw))


if __name__ == "__main__":
    main()
