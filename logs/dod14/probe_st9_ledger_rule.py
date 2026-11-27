"""ST-9 probe 2: which candidates the selection rule must survive, and what the
journal records today when a ledger carries no `## Task` block.

Both questions were raised by probe 1: `task_drift` returned an empty fingerprint for
the two ledgers that actually have a journal, so no `start` record was ever written.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402
import bugfixer_journal as journal  # noqa: E402


def main() -> None:
    pattern = gate._contract_ledger_pattern(REPO / "bug-fixer.project.md")
    files = sorted({p.resolve() for p in REPO.glob(pattern) if p.is_file()})
    print(f"candidates under {pattern!r}: {len(files)}")

    with_task: list[Path] = []
    without: list[Path] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        (with_task if gate.task_fingerprint(text) else without).append(path)

    print(f"  with a `## Task` block      : {len(with_task)}")
    print(f"  without a `## Task` block   : {len(without)}")

    print("\n== the two ledgers that carry a journal ==")
    for name in (
        "TASK-085-bugfixer-standalone-plugin.md",
        "TASK-086-bugfixer-context-cost-research.md",
    ):
        path = REPO / "tasks" / name
        text = path.read_text(encoding="utf-8", errors="replace")
        records = journal.read_events(path)
        kinds = sorted({str(r.get("event")) for r in records})
        print(f"  {name}")
        print(f"    `## Task` fingerprint : {gate.task_fingerprint(text)!r}")
        print(f"    journal events        : {kinds}")
        print(f"    `start` records       : {sum(1 for r in records if r.get('event') == 'start')}")
        print(f"    derived state         : {journal.state_of(path)}"[:120])

    print("\n== what a new `start` record would look like on a ledger with `## Task` ==")
    sample = next((p for p in with_task), None)
    if sample is not None:
        text = sample.read_text(encoding="utf-8", errors="replace")
        print(f"  sample: {sample.name}")
        print(f"    fingerprint = {gate.task_fingerprint(text)!r}")

    print("\n== candidates whose journal exists but has no `start` (would need a rule) ==")
    journalled = [p for p in files if journal.journal_path(p).is_file()]
    for path in journalled:
        has_start = any(r.get("event") == "start" for r in journal.read_events(path))
        print(f"  {path.name[:44]:46} start={has_start}")


if __name__ == "__main__":
    main()
