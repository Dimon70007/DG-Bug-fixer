"""Of the false "this is a write" classifications, how many actually become a DENY?

A classification only costs the agent anything when it turns into a refusal. `_is_code_write`
needs a target outside the allowed set, and the phase must forbid it. This probe runs the
gate's own `_decide` on the false-write commands, with a real ledger in phase 1, and counts.

The stand is a neutral temp project (not the plugin tree) — F17.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bugfixer_gate as gate  # noqa: E402
import shell_scan  # noqa: E402
from probe_st9_real_false_denies import commands  # noqa: E402

CONTRACT = """Contract version: 1
Contract verified by: agent 2026-11-28 — pytest ran

## Ledger

Task/bug file: tasks/TASK-*.md
Raw dumps: logs/dumps/

## Tests

Full suite: pytest
Test paths: tests/

## Search

Search: none

## Extra prohibitions

- no sudo
"""


def main() -> None:
    parent = Path(tempfile.mkdtemp())
    try:
        project = parent / "proj"
        for sub in ("tasks", "tests", "src", "logs/dumps"):
            (project / sub).mkdir(parents=True, exist_ok=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        (project / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
        ledger = project / "tasks" / "TASK-700.md"
        ledger.write_text("## Task\nprobe\n", encoding="utf-8")

        false_writes = [
            command
            for command in commands()
            if gate._WRITE_COMMAND_RE.search(command) and not shell_scan.really_writes(command)
        ]
        print(f"false 'write' classifications: {len(false_writes)}")

        denied: list[tuple[str, str]] = []
        allowed = 0
        for command in false_writes:
            verdict = gate._decide(
                ledger=ledger,
                tool="shell",
                args={"command": command, "cwd": str(project)},
                dev_plugin=True,
            )
            if verdict.allow:
                allowed += 1
            else:
                denied.append((command, verdict.reason))

        print(f"  → became a DENY in phase 1     : {len(denied)}")
        print(f"  → still allowed                : {allowed}")

        print("\n  reasons among the denied:")
        reasons: dict[str, int] = {}
        for _, reason in denied:
            head = reason.split(":")[0][:60]
            reasons[head] = reasons.get(head, 0) + 1
        for head, count in sorted(reasons.items(), key=lambda item: -item[1]):
            print(f"    {count:3}  {head}")

        print("\n  examples of refusals that read-only commands earn:")
        for command, reason in denied[:6]:
            print(f"    {command[:88]}")
            print(f"      → {reason[:88]}")

        print(f"\n  share of ALL commands in the corpus that are wrongly refused: "
              f"{len(denied) / len(commands()) * 100:.1f}%")
    finally:
        shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    main()
