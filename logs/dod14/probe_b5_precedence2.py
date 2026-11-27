"""Measure B-5 precedence: does a home-level contract attach a project to another project's bug?"""

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
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def measure_precedence(tmp: Path) -> None:
    home = tmp / "home"
    project = home / "github" / "my" / "repo"
    (project / "tasks").mkdir(parents=True)
    (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")
    (home / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")

    print("== B-5: a global plugin with a home-level contract ==")
    print("  layout: home/bug-fixer.project.md, project=home/github/my/repo,")
    print("          project ledger tasks/TASK-1.md, no project contract\n")

    print("  case 1: only the project has a ledger")
    try:
        found = gate.discover_ledger(project)
        print(f"    -> {rel(found, tmp)}")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: {str(exc)[:100]}")

    print("\n  case 2: home ALSO has tasks/TASK-999.md (someone else's bug)")
    (home / "tasks").mkdir(exist_ok=True)
    home_ledger = home / "tasks" / "TASK-999.md"
    home_ledger.write_text("STATUS: RUNNING\n", encoding="utf-8")
    try:
        found = gate.discover_ledger(project)
        picked = rel(found, tmp)
        verdict = "WRONG PROJECT" if found.resolve() == home_ledger.resolve() else "project's own"
        print(f"    -> {picked}   <- {verdict}")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: {str(exc)[:100]}")

    print("\n  case 3: project contract added; which one is read?")
    (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    contract = gate._find_contract(project)
    print(f"    _find_contract -> {rel(contract, tmp) if contract else None}")

    print("\n  case 4: project 2 levels below home, home contract only")
    deep = home / "a" / "b"
    (deep / "tasks").mkdir(parents=True)
    (deep / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")
    (home / "a" / "bug-fixer.project.md").unlink(missing_ok=True)
    for depth in (1, 2, 3, 4):
        probe = home.joinpath(*["a", "b", "c", "d"][:depth])
        probe.mkdir(parents=True, exist_ok=True)
        found = gate._find_contract(probe)
        print(f"    {depth} level(s) down: {'found' if found else 'MISS'}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        measure_precedence(Path(raw))


if __name__ == "__main__":
    main()
