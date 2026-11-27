"""Case 2 measured correctly: the active sub-task is older than a planned-ahead file.

The previous probe set the mtimes so the active file was newest, which proves nothing. The real risk
is the opposite: you work on sub A, never touch it during a read-only step, and sub B (created later,
not started) is newer on disk. Then the gate resolves sub B while the run belongs to sub A.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-28 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)


def touch(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def picks(project: Path) -> str:
    try:
        return gate.discover_ledger(project).name
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}"


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / "proj"
        (root / "tasks").mkdir(parents=True)
        (root / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")

        sub_a = root / "tasks" / "TASK-100.ST-1.md"
        sub_b = root / "tasks" / "TASK-100.ST-2.md"
        now = time.time()

        sub_a.write_text("## Task\nsub A — the ACTIVE run\n", encoding="utf-8")
        touch(sub_a, now - 400)
        sub_b.write_text("## Task\nsub B — planned ahead, NOT started\n", encoding="utf-8")
        touch(sub_b, now - 300)

        print("= sequential execution, the case that actually breaks =")
        print(f"  active run:      {sub_a.name}  (mtime now-400)")
        print(f"  planned ahead:   {sub_b.name}  (mtime now-300, older than the plan, newer than A)")
        print(f"  discover_ledger -> {picks(root)}")
        print()
        print("  The agent now runs a read-only step (Grep, a test run, a log dump).")
        print("  Nothing writes to the task file, so mtimes do not change.")
        print(f"  discover_ledger -> {picks(root)}")
        print()
        print("  Consequence: a deny would be journalled beside sub B and judged against")
        print("  sub B's phase — while the agent is working on sub A.")

        print("\n= does touching the active file fix it? =")
        touch(sub_a, time.time())
        print(f"  after editing {sub_a.name}: discover_ledger -> {picks(root)}")

        print("\n= what the gate does with equal mtimes =")
        sub_b.write_text("## Task\nsub B, edited\n", encoding="utf-8")
        touch(sub_b, now - 400)
        touch(sub_a, now - 400)
        print(f"  both at now-400 -> {picks(root)}")

        print("\n= summary =")
        print("  Sequential execution protects the ledger ONLY while every step touches the")
        print("  active file. Steps that write nothing (read-only research, a test run, a dump)")
        print("  are exactly when a planned-ahead file takes over.")


if __name__ == "__main__":
    main()
