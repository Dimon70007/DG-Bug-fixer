"""ST-9 probe 8: the 18 distinct refusal wordings with exact counts, so a class total is
derived rather than quoted.

probe 5 left 2 records unclassified: `tested: 0% is below … threshold` and ``tested: 92%`
cannot be verified`. Both are §11a rules, which only apply to a `CHANGELOG.md` write, so they
belong to that class — which would make it 5, not the 3 TASK-086 states. This probe prints
every wording and lets the count decide.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LEDGER = REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"


def main() -> None:
    path = Path(str(LEDGER) + ".bugfixer.jsonl")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counter = Counter(record["why"] for record in records)
    print(f"records={len(records)} distinct wordings={len(counter)}\n")
    for number, (why, count) in enumerate(counter.most_common(), start=1):
        tools = Counter(r["tool"] for r in records if r["why"] == why)
        print(f"[{number:2}] n={count:3} tools={dict(tools)}")
        print(f"     {why[:150]}")

    print("\n== why a `tested:` refusal is a CHANGELOG refusal ==")
    gate = (REPO / "skills" / "bug-fixer" / "hooks" / "bugfixer_gate.py").read_text(encoding="utf-8")
    for token in ("_changelog_problem(", "_docs_only_problem(", "tested:"):
        print(f"  {token!r} appears {gate.count(token)} times in bugfixer_gate.py")
    for number, line in enumerate(gate.splitlines(), start=1):
        if "_changelog_problem(" in line:
            print(f"    bugfixer_gate.py:{number}: {line.strip()}")


if __name__ == "__main__":
    main()
