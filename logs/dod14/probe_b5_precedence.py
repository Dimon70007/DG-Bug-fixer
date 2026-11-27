"""Measure B-5 precedence and B-9 (is "written, not verified" enforceable anywhere?)."""

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


def measure_precedence(tmp: Path) -> None:
    print("== B-5: nearest contract wins? ==")
    home = tmp / "home"
    project = home / "github" / "my" / "repo"
    (project / "tasks").mkdir(parents=True)
    ledger = project / "tasks" / "TASK-1.md"
    ledger.write_text("STATUS: RUNNING\n", encoding="utf-8")
    (home / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    project_contract = project / "bug-fixer.project.md"

    print("  home contract only, project has none, project ledger exists:")
    try:
        found = gate.discover_ledger(project)
        print(f"    ledger -> {found.relative_to(tmp)}")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: {str(exc)[:90]}")

    print("  same, but the home ALSO has tasks/TASK-*.md (a different bug):")
    (home / "tasks").mkdir(exist_ok=True)
    (home / "tasks" / "TASK-999.md").write_text("STATUS: DONE\n", encoding="utf-8")
    try:
        found = gate.discover_ledger(project)
        print(f"    ledger -> {found.relative_to(tmp)}   <- which bug is this?")
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: {str(exc)[:90]}")

    print("  project contract added on top of the home one:")
    project_contract.write_text(CONTRACT.replace("tasks/TASK-*.md", "tasks/TASK-*.md"), encoding="utf-8")
    contract = gate._find_contract(project)
    print(f"    _find_contract -> {contract.relative_to(tmp) if contract else None}")


def measure_unverified(tmp: Path) -> None:
    print("\n== B-9: is there a way to record 'written, not verified'? ==")
    text = (SKILL.parent.parent / "docs/bug-fixer-v3-spec.md").read_text(encoding="utf-8")
    for needle in ("не проверено", "непроверен", "Not executed", "not verified", "unverified"):
        hits = [i + 1 for i, line in enumerate(text.splitlines()) if needle.lower() in line.lower()]
        print(f"  spec mentions {needle!r}: {hits or 'no'}")

    statuses = ["RUNNING", "DONE", "PARTIAL", "LIMIT_REACHED", "BLOCKED"]
    print(f"  spec §9 statuses: {statuses}")
    print(f"  any 'UNVERIFIED' status: {'UNVERIFIED' in text}")

    print("\n  does any test or gate code know about unverified claims?")
    import subprocess

    out = subprocess.run(
        ["rg", "-n", "-i", "-e", "не проверено", "-e", "not executed", "-e", "unverified",
         "--glob", "!logs/**", "--glob", "!*.jsonl", str(SKILL)],
        text=True, capture_output=True)
    for line in out.stdout.strip().splitlines()[:14]:
        print(f"    {line.replace(str(SKILL) + '/', '')}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        measure_precedence(Path(raw))
        measure_unverified(Path(raw))


if __name__ == "__main__":
    main()
