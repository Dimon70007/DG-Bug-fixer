"""ST-9 probe 5: re-derive the F1/F2 class split from the journal, so the project cites
numbers that came from a run in this session rather than from an earlier summary.

Classification is by exact `why` prefix, and every record must land in exactly one class —
the totals are printed last so a missing class shows up as a sum mismatch.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3].parent if False else Path(__file__).resolve().parents[4]
LEDGER = REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"

#: Ordered: the first matching rule wins, so the specific classes come before the generic.
RULES: tuple[tuple[str, str], ...] = (
    ("journal-write", r"written by the gate, not by the agent"),
    ("outside-project", r"writes outside the project"),
    ("changelog-evidence", r"CHANGELOG"),
    ("unknown-tool", r"neither a known write tool"),
    ("phase-ban", r"Phase \d+ forbids production-code changes"),
)

OLD_HINT = "bugfixer_gate_state.py"
NEW_HINT = r"/bin/bugfixer"


def classify(why: str) -> tuple[str, str]:
    for name, pattern in RULES:
        if re.search(pattern, why):
            if name == "phase-ban":
                hint = "old-hint" if OLD_HINT in why else "correct-hint"
                return name, hint
            return name, "-"
    return "UNCLASSIFIED", "-"


def main() -> None:
    path = Path(str(LEDGER) + ".bugfixer.jsonl")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"journal: {path.name}")
    print(f"records: {len(records)}")
    print(f"events : {Counter(r['event'] for r in records)}")
    print(f"tools  : {Counter(r.get('tool') for r in records)}")

    classes: Counter[str] = Counter()
    hints: Counter[str] = Counter()
    hours: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        name, hint = classify(record["why"])
        classes[name] += 1
        if name == "phase-ban":
            hints[hint] += 1
            hours[hint][record["at"][11:13]] += 1
    print(f"\nclasses: {dict(classes)}")
    print(f"  sum over classes: {sum(classes.values())} (must equal records)")
    print(f"\nphase-ban split: {dict(hints)}")
    for label, counter in hours.items():
        print(f"  {label:12} by hour: {dict(sorted(counter.items()))}")

    print(f"\nunclassified records: {classes['UNCLASSIFIED']}")
    print(f"distinct `why` strings: {len({r['why'] for r in records})}")

    print("\n== both journals, for the F9 count ==")
    for other in sorted((REPO / "tasks").glob("*.bugfixer.jsonl")):
        lines = [json.loads(x) for x in other.read_text(encoding="utf-8").splitlines() if x.strip()]
        print(f"  {other.name[:52]:54} {len(lines):3} records, {lines[0]['at']} .. {lines[-1]['at']}")


if __name__ == "__main__":
    main()
