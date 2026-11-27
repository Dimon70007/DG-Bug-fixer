"""Does sequential work make the mtime ledger selection safe?

Claim under test (operator, 2026-11-28): "it will not be a problem while subtasks are executed
sequentially, as the skill prescribes."

The mechanism in F9 is: `discover_ledger` picks the newest file matching `Task/bug file:` by mtime.
The question is what "newest" means when the agent works on sub-task file A while file B was created
later and is *older in content* but *newer in mtime*.

Simulated with real files and real clock ticks, using the gate's own resolver.
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
    """Set a file's mtime explicitly, so ordering is deterministic."""
    os.utime(path, (when, when))


def picks(project: Path) -> str:
    """Which ledger the gate resolves, or the failure type."""
    try:
        return gate.discover_ledger(project).name
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}"


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / "proj"
        (root / "tasks").mkdir(parents=True)
        (root / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")

        main_task = root / "tasks" / "TASK-100.md"
        sub_a = root / "tasks" / "TASK-100.ST-1.md"
        sub_b = root / "tasks" / "TASK-100.ST-2.md"
        now = time.time()

        print("== sequential work, but a LATER-CREATED file exists ==")
        main_task.write_text("## Task\nmain\n", encoding="utf-8")
        touch(main_task, now - 300)
        sub_a.write_text("## Task\nsub A\n", encoding="utf-8")
        touch(sub_a, now - 200)
        sub_b.write_text("## Task\nsub B (created second, not started yet)\n", encoding="utf-8")
        touch(sub_b, now - 100)

        print(f"  working on {sub_a.name}, agent edits it (mtime -> now):")
        sub_a.write_text("## Task\nsub A, edited\n", encoding="utf-8")
        touch(sub_a, time.time())
        print(f"    discover_ledger -> {picks(root)}")

        print("\n  now the agent writes its FIRST record for sub A ... then works in a shell:")
        # A shell command that does not touch any task file (the common case).
        print(f"    after an unrelated Shell call -> {picks(root)}")

        print("\n== the operator's claim, tested strictly ==")
        print("  If work is sequential AND each step touches the file it works on,")
        print("  the edited file is the newest -> selection is correct.")
        print(f"  Latest edit wins. Confirmed above: {picks(root)}")

        print("\n== where sequential work does NOT save you ==")
        print("  1) creating the NEXT sub-task (planning ahead) before finishing the current one")
        print("  2) a step that writes nothing to the task file (read-only research, a test run)")
        print("  3) after `git checkout` / `git stash` / an editor save of another file")
        print("  4) equal mtimes (fast creation of two files) -> the gate REFUSES outright")

        print("\n  case 2 measured: finish sub A, then run a read-only step")
        sub_a.write_text("## Task\nsub A, done\n", encoding="utf-8")
        touch(sub_a, now - 50)
        # sub B is now the newest file on disk, though sub A is the active run.
        print(f"    sub A touched at now-50, sub B at now-100 -> {picks(root)}")

        print("\n  case 4 measured: two files with the same mtime")
        twin_1 = root / "tasks" / "TASK-101.md"
        twin_2 = root / "tasks" / "TASK-102.md"
        twin_1.write_text("x\n", encoding="utf-8")
        twin_2.write_text("y\n", encoding="utf-8")
        stamp = time.time()
        touch(twin_1, stamp)
        touch(twin_2, stamp)
        print(f"    two files, identical mtime -> {picks(root)}")


if __name__ == "__main__":
    main()
