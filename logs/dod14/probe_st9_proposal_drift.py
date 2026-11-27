"""Why does the proposal flag 1317 commands when the gate flags 544?

A model that flags MORE than the thing it replaces is not a refinement — it is a different bug.
This probe finds which rule inside `proposed_is_write` fires, for the commands where the
proposal and the gate disagree, so the flaw is named instead of guessed.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bugfixer_gate as gate  # noqa: E402
import shell_scan  # noqa: E402
from probe_st9_proposed_rule import proposed_is_write
from probe_st9_real_false_denies import commands  # noqa: E402


def reason(command: str) -> str:
    if shell_scan.redirection_targets(command):
        return "redirection"
    if shell_scan.write_verbs(command):
        return "write-verb"
    return "nested"


def main() -> None:
    extra = [
        command
        for command in commands()
        if proposed_is_write(command) and not gate._WRITE_COMMAND_RE.search(command)
    ]
    print(f"commands the proposal flags but the gate does not: {len(extra)}")
    counts = Counter(reason(command) for command in extra)
    print(f"  by rule: {dict(counts)}")

    print("\n  redirection cases (the suspicious ones):")
    shown = 0
    for command in extra:
        if reason(command) != "redirection":
            continue
        targets = shell_scan.redirection_targets(command)
        print(f"    targets={targets}")
        print(f"      {command[:130]}")
        shown += 1
        if shown >= 8:
            break

    print("\n  write-verb cases:")
    shown = 0
    for command in extra:
        if reason(command) != "write-verb":
            continue
        print(f"    verbs={shell_scan.write_verbs(command)}  {command[:110]}")
        shown += 1
        if shown >= 8:
            break


if __name__ == "__main__":
    main()
