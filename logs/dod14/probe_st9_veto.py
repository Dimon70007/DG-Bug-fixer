"""The only number that can veto the simplification: of the commands the minimal rule stops
calling a write, how many REALLY write?

Adjudicated by `shell_scan` (independent character-level parser), and every hit printed in full
so it can be checked by eye. If this prints zero, the change loses no protection on this corpus;
if it prints any, the change is a bypass and must not ship.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bugfixer_gate as gate  # noqa: E402
import shell_scan  # noqa: E402
from probe_st9_minimal_variant import minimal_is_write  # noqa: E402
from probe_st9_real_false_denies import commands  # noqa: E402


def main() -> None:
    every = commands()
    dropped = [
        command
        for command in every
        if gate._WRITE_COMMAND_RE.search(command) and not minimal_is_write(command)
    ]
    print(f"commands the minimal rule stops treating as writes: {len(dropped)}")

    real = [command for command in dropped if shell_scan.really_writes(command)]
    print(f"  of those, REALLY write (by the independent parser): {len(real)}")
    for command in real:
        print(f"    targets={shell_scan.redirection_targets(command)} verbs={shell_scan.write_verbs(command)}")
        print(f"      {command[:160]}")

    print("\n  the rest, by first 60 chars, to confirm they are reads:")
    for command in dropped[:15]:
        print(f"    {command[:100]}")


if __name__ == "__main__":
    main()
