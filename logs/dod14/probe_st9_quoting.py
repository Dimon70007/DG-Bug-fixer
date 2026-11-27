"""ST-9 probe 7: quoting does not protect a read-only command from being classified as a write.

Measured twice in this session (journal of TASK-086):
  * 22:11:55Z — `python -c "print('a -> b')"` denied; the arrow contains `>`.
  * 22:19Z    — `grep -rn "chdir\\|mkdir|write_text" logs/ ...` denied; the quoted pattern
                contains `|mkdir`, and `|` + a write-verb name is read as a shell pipeline.

`_WRITE_COMMAND_RE` scans the raw command string, so any `>` and any `|<write-verb>` that
happens to sit inside a quoted argument is indistinguishable from real shell syntax.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CASES = [
    (
        'grep -rn "chdir\\|mkdir|write_text" logs/',
        "the command refused at 22:19 — a quoted alternation containing |mkdir",
    ),
    ("./x.py -c \"print('a -> b')\"", "the command refused at 22:11 — an arrow in a string"),
    ("rg 'a|mkdir' hooks/", "a quoted alternation with a write verb"),
    ("rg 'x>>y' docs/", "a quoted double redirect"),
    ("rg 'chdir|mkdir' logs/", "same quote, no backslash"),
    ("pytest -q 2>/dev/null", "the corpus payload: discard target is exempt"),
    ("echo 'rm -rf x'", "a quoted destructive verb, never executed by echo"),
    ("true && mkdir build", "a real write behind a real pipeline separator"),
]


def main() -> None:
    print("classified write? / destinations the gate would enforce")
    for command, label in CASES:
        is_write = bool(gate._WRITE_COMMAND_RE.search(command))
        targets = gate._shell_write_targets(command)
        print(f"  {label[:56]:58} write={is_write!s:5} targets={targets}")

    print("\n== the two constructs responsible ==")
    print("  redirection : >>?\\s*(?!(?:/dev/(?:null|zero|std..)))[^&|\\s]")
    print("  pipeline    : (?:^|[;&|]\\s*)(?:rm|mv|cp|mkdir|…)\\b")

    print("\n== price of one such refusal, from the journal ==")
    import json
    import statistics

    journal = REPO / "tasks" / "TASK-086-bugfixer-context-cost-research.md.bugfixer.jsonl"
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    reasons = [len(record["why"]) for record in records]
    print(f"  records in TASK-086's journal: {len(records)}")
    print(f"  median `why` length: {int(statistics.median(reasons))} chars")
    print(f"  the refusal is re-sent as agent_message and user_message: ~{2 * int(statistics.median(reasons))} chars per deny")


if __name__ == "__main__":
    main()
