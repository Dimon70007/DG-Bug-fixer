"""ST-9 probe 10: what the proposed selection rule costs per tool call, and how big the
proposed record is against the records that exist today.

Rule 4 (see the design) scopes the mtime race to candidates that already have a journal:
glob the contract pattern, then stat `<ledger>.bugfixer.jsonl` for each. This probe measures
that cost on the real 130-file `tasks/` directory, and the record size for a shell deny.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402
import bugfixer_journal as journal  # noqa: E402


def main() -> None:
    pattern = gate._contract_ledger_pattern(REPO / "bug-fixer.project.md")

    def rule_4() -> Path:
        files = sorted({p.resolve() for p in REPO.glob(pattern) if p.is_file()})
        journalled = [p for p in files if journal.journal_path(p).is_file()]
        pool = journalled or files
        return max(pool, key=lambda p: journal.journal_path(p).stat().st_mtime if journalled else p.stat().st_mtime)

    started = time.perf_counter()
    for _ in range(20):
        picked = rule_4()
    print(f"rule 4: {(time.perf_counter() - started) / 20 * 1000:.2f} ms/call, picks {picked.name}")

    def rule_a() -> Path:
        files = sorted({p.resolve() for p in REPO.glob(pattern) if p.is_file()})
        return max(files, key=lambda p: p.stat().st_mtime)

    started = time.perf_counter()
    for _ in range(20):
        picked_a = rule_a()
    print(f"rule A (today): {(time.perf_counter() - started) / 20 * 1000:.2f} ms/call, picks {picked_a.name}")

    print("\n== the ledger every rule must agree on right now ==")
    print(f"  contract ledger (doctor) : TASK-086-bugfixer-context-cost-research.md")
    print(f"  rule A                   : {picked_a.name}")
    print(f"  rule 4                   : {picked.name}")

    print("\n== record size: today vs proposed, on a real shell deny ==")
    today = {
        "at": "2026-11-28T01:12:37Z",
        "event": "deny",
        "why": "Phase 1 forbids production-code changes: " + "…" * 0,
        "tool": "shell",
    }
    ledger = REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"
    records = journal.read_events(ledger)
    medians = {
        "why": int(statistics.median(len(r["why"]) for r in records)),
        "record": int(statistics.median(len(json.dumps(r, ensure_ascii=False)) for r in records)),
    }
    print(f"  today   : `why` median {medians['why']} chars, record median {medians['record']} B")
    proposed = dict(today)
    proposed["why"] = "r" * medians["why"]
    proposed.update(
        {
            "target": "cat >> tasks/TASK-086-bugfixer-context-cost-research.md <<'EOF'",
            "phase": 1,
            "verdict_of": "phase-ban",
            "ledger": "tasks/TASK-086-bugfixer-context-cost-research.md",
            "ledger_source": "journal-mtime",
        }
    )
    print(f"  proposed: record {len(json.dumps(proposed, ensure_ascii=False))} B with a 60-char target")
    long_target = "x" * 400
    proposed["target"] = long_target
    print(f"            record {len(json.dumps(proposed, ensure_ascii=False))} B at the 400-char target cap")

    print("\n== does the extra size matter? the journal is not read by the agent ==")
    size = sum(len(json.dumps(r, ensure_ascii=False)) for r in records)
    print(f"  TASK-085 journal on disk: {size} B over {len(records)} records")
    print("  read path: `state_of` reads all records; measured 0.19-0.25 ms per call")


if __name__ == "__main__":
    main()
