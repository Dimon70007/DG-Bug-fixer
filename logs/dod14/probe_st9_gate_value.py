"""How much did the gate actually prevent, and how much did it cost?

Counts every denial in both real journals and splits them by what is provable:

* **provably false** — the target is known to be a read-only command (F12/F22) or a path from
  the gate's own test fixtures (F6);
* **provably correct** — a rule the project does want (journal write, CHANGELOG without evidence);
* **unmeasurable** — the target was never recorded (F5/F10), so nobody can say.

The unmeasurable bucket is the honest headline: the gate cannot be judged because it does not
record what it refused.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]

PHASE_BAN = re.compile(r"Phase \d+ forbids production-code changes")
OUTSIDE = re.compile(r"writes outside the project \(([^)]*)\)")
JOURNAL = re.compile(r"written by the gate, not by the agent")
CHANGELOG = re.compile(r"CHANGELOG")
TESTED = re.compile(r"`tested: ")
UNKNOWN = re.compile(r"neither a known write tool")

#: Targets that F6 proved came from the gate's own test fixtures, not from a real attempt.
FIXTURE_TARGETS = ("/etc/passwd", "/tmp/x", "/tmp/x.py", "/tmp/x/", "/tmp/x\",", "/tmp/x',",
                   "/tmp/x'},\"", "/tmp/x')", "/tmp\",", "/skills\\", "/x/lib/probe.py", "/tmp/nodes.txt", "/")


def classify(why: str) -> str:
    if JOURNAL.search(why):
        return "correct: journal write"
    if TESTED.search(why) or CHANGELOG.search(why):
        return "correct: CHANGELOG evidence"
    if UNKNOWN.search(why):
        return "correct: unclassifiable tool (fail-closed)"
    match = OUTSIDE.search(why)
    if match:
        target = match.group(1).strip()
        if any(target.startswith(item) for item in FIXTURE_TARGETS):
            return "provably not an attempt (gate's own test fixture)"
        return "outside project (target unknown origin)"
    if PHASE_BAN.search(why):
        return "unmeasurable: phase ban, target not recorded"
    return "other"


def main() -> None:
    rows: list[tuple[str, str, str]] = []
    for name in (
        "TASK-085-bugfixer-standalone-plugin.md",
        "TASK-086-bugfixer-context-cost-research.md",
    ):
        journal = REPO / "tasks" / (name + ".bugfixer.jsonl")
        if not journal.is_file():
            continue
        for line in journal.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            rows.append((name[:14], classify(record["why"]), record["why"][:60]))

    print(f"denials in both journals: {len(rows)}\n")
    buckets: dict[str, list[int]] = {}
    for name, label, _ in rows:
        buckets.setdefault(label, []).append(1)
    for label, hits in sorted(buckets.items(), key=lambda item: -len(item[1])):
        print(f"  {len(hits):4}  {label}")

    correct = sum(len(v) for k, v in buckets.items() if k.startswith("correct"))
    provably_false = sum(len(v) for k, v in buckets.items() if k.startswith("provably"))
    unmeasurable = sum(len(v) for k, v in buckets.items() if k.startswith("unmeasurable"))

    print()
    print(f"  correct                       : {correct}")
    print(f"  provably not a real attempt   : {provably_false}")
    print(f"  unmeasurable (target unknown) : {unmeasurable}")
    print()
    if rows:
        print(f"  measured precision (correct / all): {correct / len(rows) * 100:.1f}%")

    print("\n== what the gate cost, in model turns ==")
    print(f"  {len(rows)} denials; each one is at least one extra turn (F21)")
    print("  a turn re-sends the whole context; the ledger it sends was measured at 60 555 tokens")
    print(f"  lower bound on input tokens: {len(rows)} x 60 555 = {len(rows) * 60555 / 1_000_000:.2f} млн")


if __name__ == "__main__":
    main()
