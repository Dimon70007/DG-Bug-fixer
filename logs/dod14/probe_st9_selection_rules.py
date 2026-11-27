"""ST-9 probe 9: the selection rule the project can actually support.

Three candidate rules were named in ST-9. This probe measures what each one sees on the real
`tasks/` directory, so the design cites numbers instead of preferences.

Rule A — newest mtime (today).
Rule B — "the ledger whose journal says RUNNING" (no journal = not started).
Rule C — the ledger named by the call itself: a write whose target is a `tasks/TASK-*.md` file.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
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
    print(f"candidates: {len(files)} matching {pattern!r}")

    print("\n== rule A: newest mtime ==")
    newest = max(files, key=lambda p: p.stat().st_mtime)
    print(f"  picks {newest.name}")
    print("  measured defect (F9): creating any task file re-points the run")

    print("\n== rule B: has a journal / last record says RUNNING ==")
    with_journal = [p for p in files if journal.journal_path(p).is_file()]
    print(f"  candidates with a journal: {len(with_journal)}")
    for path in with_journal:
        events = journal.read_events(path)
        state = journal.state_of(path)
        print(f"    {path.name[:40]:42} records={len(events):3} phase={state['phase']} status={state['status']}")
    running = []
    for path in with_journal:
        if journal.state_of(path)["status"] == "RUNNING":
            running.append(path)
    print(f"  resolved by rule B alone: {len(running)} -> {'ambiguous, must refuse' if len(running) > 1 else 'deterministic'}")

    print("\n== rule C: the call names the ledger ==")
    cases = [
        ("write", str(REPO / "tasks" / "TASK-086-bugfixer-context-cost-research.md")),
        ("shell", f"cat >> {REPO}/tasks/TASK-086-bugfixer-context-cost-research.md <<'EOF'"),
        ("write", str(REPO / "skills" / "bug-fixer" / "hooks" / "bugfixer_gate.py")),
    ]
    for tool, detail in cases:
        hit = next((p for p in files if gate._addresses_ledger(detail, p) and p.name.startswith("TASK-0")), None)
        named = [p.name for p in files if gate._addresses_ledger(detail, p)]
        print(f"  {tool:6} names a ledger? {named or 'no'}  (first match: {hit.name if hit else 'none'})")

    print("\n== what rule C cannot see ==")
    print("  a read-only step names nothing, so the pin from the last named write must persist")
    print("  a `delete` call raises UnknownToolError before any target is extracted:")

    path = REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"
    records = journal.read_events(path)
    tools = Counter(str(r.get("tool")) for r in records)
    print(f"    in the TASK-085 journal: {dict(tools)}")
    print(f"    records that could carry no target: {tools.get('delete', 0)} of {len(records)}")

    print("\n== the price of a refusal today ==")
    reasons = [len(r["why"]) for r in records]
    print(f"  `why` median {int(sorted(reasons)[len(reasons) // 2])} chars; `_emit` sends the same")
    print("  string as both `agent_message` and `user_message` — two copies per denial.")

    print("\n== is `task_drift` reachable in this repository? ==")
    with_task = [p for p in files if gate.task_fingerprint(p.read_text(encoding="utf-8", errors="replace"))]
    print(f"  candidates with a `## Task` block: {len(with_task)} of {len(files)}")
    print("  -> no `start` record is ever written here, so the drift warning never fires")


if __name__ == "__main__":
    main()
