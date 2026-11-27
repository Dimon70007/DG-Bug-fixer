"""What the payload root is used FOR, and whether it is the plugin or the project.

Three call sites consume `_project_root(event)`:
  1. `_resolve_ledger` -> `discover_ledger(root)`: where to look for the contract/ledger.
  2. `_unblocks_setup(...)`: which relative paths count as setup work (root / target).
  3. `_contract_placeholders(root)`: which contract to read the fields from.

Every other boundary — which writes are inside the project (`_project_of(ledger)`) and which
are the plugin's own tree (`_is_plugin_file` via path markers) — does NOT use it.
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


def measure_plugin_vs_project(tmp: Path) -> None:
    project = tmp / "myproject"
    (project / "tasks").mkdir(parents=True)
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")

    installed = project / ".cursor" / "skills" / "bug-fixer"
    (installed / "hooks").mkdir(parents=True)
    (installed / "SKILL.md").write_text("---\nname: bug-fixer\n---\n", encoding="utf-8")

    print("== 1. what the payload root names ==")
    for label, event in (
        ("Write from project root", {"cwd": str(project), "workspace_roots": [str(project)]}),
        ("Write with cwd=null", {"cwd": None, "workspace_roots": [str(project)]}),
        ("Shell from a subdir", {"cwd": str(project / "src" / "deep"), "workspace_roots": [str(project)]}),
        ("hook run inside installed tree", {"cwd": str(installed), "workspace_roots": [str(installed)]}),
    ):
        root = gate._project_root(event)
        print(f"  {label:<32} -> {root.relative_to(tmp)}")

    print("\n== 2. the plugin's own tree is recognised by path markers, not by root ==")
    for tool_path in (
        ".cursor/skills/bug-fixer/hooks/run-gate.sh",
        ".cursor/skills/bug-fixer/lib/bugfixer/contract.py",
    ):
        absolute = str(project / tool_path)
        as_plugin = gate._is_plugin_file(absolute, project, dev_plugin=False)
        with_dev = gate._is_plugin_file(absolute, project, dev_plugin=True)
        print(f"  {tool_path:<46} plugin={as_plugin!s:<5} with --dev-plugin={with_dev}")

    print("\n== 3. the write boundary is the CONTRACT's directory, not the payload root ==")
    ledger = project / "tasks" / "TASK-1.md"
    print(f"  _project_of(ledger) -> {gate._project_of(ledger).relative_to(tmp)}")
    print("  (searching upward for bug-fixer.project.md — the payload root is not consulted)")

    print("\n== 4. consequence: does a deep Shell cwd change WHERE the plugin thinks it is? ==")
    deep = project / "src" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    event = {"cwd": str(deep), "workspace_roots": [str(project)]}
    root = gate._project_root(event)
    print(f"  root={root.relative_to(tmp)}")
    print(f"  is this the project?  {root == project}")
    print(f"  is this the plugin?   {root == installed}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        measure_plugin_vs_project(Path(raw))


if __name__ == "__main__":
    main()
