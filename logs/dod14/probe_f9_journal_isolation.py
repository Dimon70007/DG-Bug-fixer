"""F9: do the gate's own tests write into a LIVE ledger's journal?

Mechanism suspected: tests call `gate.decide(ledger=<temp project>)`, and the journal path is derived
from the ledger. If the temp project sits inside the repository, the journal lands beside a real task
file.

This measures line counts before and after running one boundary test that uses an outside-project
target (the exact shape of the 13:00 cluster in TASK-085's journal).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PLUGIN = REPO / "skills" / "bug-fixer"
PYTHON = Path("/Users/otvazhniy/github/my/linux_to_mac/.venv/bin/python")

WATCH = [
    REPO / "tasks" / "TASK-086-bugfixer-context-cost-research.md",
    REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md",
]


def lines(path: Path) -> int:
    """Journal line count for a ledger, or 0 when the journal does not exist yet."""
    journal = path.with_suffix(path.suffix + ".bugfixer.jsonl")
    if not journal.is_file():
        return 0
    return len([l for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()])


def main() -> None:
    before = {p.name: lines(p) for p in WATCH}
    print("before:")
    for name, count in before.items():
        print(f"  {name}: {count} journal line(s)")

    test = "tests/test_bugfixer_project_boundary_v3.py::test_a_write_into_a_system_directory_is_refused"
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", test, "-p", "no:warnings", "-q"],
        cwd=str(PLUGIN), text=True, capture_output=True,
    )
    print(f"\nran {test.split('::')[1]}: exit={proc.returncode}")

    after = {p.name: lines(p) for p in WATCH}
    print("\nafter:")
    grew = False
    for name, count in after.items():
        delta = count - before[name]
        if delta:
            grew = True
        print(f"  {name}: {count} line(s)  delta={delta:+d}")

    if grew:
        print("\nVERDICT: a gate test wrote into a LIVE journal (F9 CONFIRMED)")
        for p in WATCH:
            journal = p.with_suffix(p.suffix + ".bugfixer.jsonl")
            if journal.is_file() and lines(p) > before[p.name]:
                print(f"  newest lines in {journal.name}:")
                for line in journal.read_text(encoding="utf-8").splitlines()[-3:]:
                    print(f"    {line[:140]}")
    else:
        print("\nVERDICT: live journals untouched by this test (F9 not reproduced this way)")


if __name__ == "__main__":
    main()
