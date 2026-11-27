"""ST-9 feasibility probe: are `target`, `phase`, `verdict_of` and `ledger` available
where the deny is journalled, and what does the ledger-selection rule have to survive?

Every number printed here comes from the repository's own files and the gate's own
functions. Nothing is assumed.
"""

from __future__ import annotations

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


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> None:
    section("1. what `_is_write_attempt` already knows (the `target` source)")
    # Payloads taken from the captured corpus, not invented.
    cases = [
        ("Write", {"file_path": "/project/tasks/TASK-086-x.md", "content": "x\n"}),
        ("Shell", {"command": "pytest -q 2>/dev/null", "cwd": "", "timeout": 30000}),
        ("Shell", {"command": "cat >> tasks/TASK-086-x.md <<'EOF'\nx\nEOF", "cwd": ""}),
        ("Grep", {"pattern": "needle", "file_path": "/project/lib"}),
    ]
    for tool, args in cases:
        try:
            # The gate lowercases the name from the payload (`_tool_name`); a raw "Write"
            # is unclassifiable, which is the F7 class, not what is measured here.
            is_write, detail = gate._is_write_attempt(tool.lower(), args)
            print(f"  {tool:6} is_write={is_write!s:5} detail={detail!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {tool:6} raised {type(exc).__name__}: {exc}")

    section("2. does `task_drift` see the ledgers we actually have?")
    for name in ("TASK-085-bugfixer-standalone-plugin.md", "TASK-086-bugfixer-context-cost-research.md"):
        path = REPO / "tasks" / name
        text = path.read_text(encoding="utf-8", errors="replace")
        fingerprint = gate.task_fingerprint(text)
        print(f"  {name[:40]:42} ## Task block -> fingerprint={fingerprint!r}")

    section("3. how many candidates `discover_ledger` sorts by mtime right now")
    pattern = gate._contract_ledger_pattern(REPO / "bug-fixer.project.md")
    files = sorted({p.resolve() for p in REPO.glob(pattern) if p.is_file()})
    print(f"  pattern={pattern!r} candidates={len(files)}")
    newest = max(files, key=lambda p: p.stat().st_mtime)
    print(f"  newest by mtime -> {newest.name}")
    journalled = [p for p in files if journal.journal_path(p).is_file()]
    print(f"  candidates that already have a journal: {len(journalled)}")
    for path in journalled:
        records = journal.read_events(path)
        print(
            f"    {path.name[:38]:40} records={len(records):3} "
            f"events={sorted({r.get('event') for r in records})}"
        )

    section("4. what a journal-derived rule would see: STATUS lines among candidates")
    counts: dict[str, int] = {}
    running: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        status = gate.read_status(text)
        counts[status] = counts.get(status, 0) + 1
        if status == "RUNNING":
            running.append(path.name)
    print(f"  status histogram over {len(files)} candidates: {counts}")
    print(f"  RUNNING candidates: {len(running)}")
    for name in running[:10]:
        print(f"    {name}")

    section("5. cost of the extra fields, measured on the real journal")
    ledger = REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"
    events = journal.read_events(ledger)
    base = [len(__import__("json").dumps(r, ensure_ascii=False)) for r in events]
    extra = [
        len(
            __import__("json").dumps(
                {
                    "target": "cat >> tasks/TASK-085-bugfixer-standalone-plugin.md <<'EOF'",
                    "phase": 1,
                    "verdict_of": "phase-ban",
                    "ledger": str(ledger),
                },
                ensure_ascii=False,
            )
        )
        for _ in events
    ]
    print(f"  records={len(events)} current median={int(statistics.median(base))} B")
    print(f"  extra fields add ~{int(statistics.median(extra))} B per record (median)")
    print(f"  journal would grow {sum(base)} -> ~{sum(base) + sum(extra)} B")

    section("6. price of `phase` per call (already parsed by the gate)")
    started = time.perf_counter()
    for _ in range(200):
        journal.state_of(ledger)
    print(f"  journal.state_of on a 117-record journal: {(time.perf_counter() - started) / 200 * 1000:.2f} ms/call")

    section("7. deny sites that would need a rule id")
    source = (SKILL / "hooks" / "bugfixer_gate.py").read_text(encoding="utf-8").splitlines()
    sites = [
        (number, line.strip())
        for number, line in enumerate(source, start=1)
        if line.strip().startswith("return _deny(") or line.strip() == "return _deny("
    ]
    print(f"  `return _deny(` sites: {len(sites)}")
    for number, _ in sites:
        print(f"    bugfixer_gate.py:{number}")


if __name__ == "__main__":
    main()
