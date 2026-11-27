"""The 34 remaining "real writes" the candidate rule misses — are they really writes, and what
exactly are they?

Two shapes were visible: `git add` (never in the gate's verb list, so this is a pre-existing
gap, not a regression) and `.venv/bin/python - <<'PY'` (an interpreter whose path ends in a
version directory, so `python3?` does not match it).

This probe splits the 34 by cause and shows whether tightening the heredoc pattern closes it.
"""

from __future__ import annotations

import re
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
from probe_st9_heredoc_rule import candidate, has_heredoc  # noqa: E402
from probe_st9_minimal_variant import minimal_is_write  # noqa: E402
from probe_st9_real_false_denies import commands  # noqa: E402

#: Any interpreter path with an optional version or venv prefix, fed a heredoc.
HEREDOC_ANY_RE = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1",
    re.MULTILINE,
)


def widened(command: str) -> bool:
    """Candidate rule, with `<<'X'` counted whenever the segment runs a program.

    Args:
        command: Raw command line.

    Returns:
        True when the command writes or feeds a heredoc to any program.
    """
    if candidate(command):
        return True
    return bool(HEREDOC_ANY_RE.search(command))


def cause(command: str) -> str:
    verbs = shell_scan.write_verbs(command)
    if "git add" in verbs:
        return "git add (never in the gate's verb list)"
    if shell_scan.redirection_targets(command):
        return "heredoc body read as a target"
    if has_heredoc(command):
        return "heredoc (should have been caught)"
    if "<<" in command:
        return "heredoc with an interpreter path the pattern misses"
    return "other"


def main() -> None:
    every = commands()
    flagged_now = [c for c in every if gate._WRITE_COMMAND_RE.search(c)]
    dropped = [c for c in flagged_now if not candidate(c)]
    real = [c for c in dropped if shell_scan.really_writes(c)]

    counts = Counter(cause(command) for command in real)
    print(f"remaining 'real writes' missed by the candidate: {len(real)}")
    for label, count in counts.most_common():
        print(f"  {count:3}  {label}")

    print("\n== with the widened heredoc rule ==")
    flagged_widened = [c for c in every if widened(c)]
    dropped_w = [c for c in flagged_now if not widened(c)]
    real_w = [c for c in dropped_w if shell_scan.really_writes(c)]
    print(f"  gate today           : {len(flagged_now)}")
    print(f"  widened rule         : {len(flagged_widened)}")
    print(f"  no longer a write    : {len(dropped_w)}")
    print(f"  of those, really write: {len(real_w)}")
    counts_w = Counter(cause(command) for command in real_w)
    for label, count in counts_w.most_common():
        print(f"    {count:3}  {label}")
    for command in real_w[:5]:
        print(f"      {command[:150]}")

    print("\n== is `git add` a regression? ==")
    print("  the gate's own regex, on `git add x`:")
    print(f"    gate says write: {gate._WRITE_COMMAND_RE.search('git add x') is not None}")
    print("  -> the gate never treated `git add` as a write; the change does not lose it")
    print("  -> counting it would make the gate STRICTER, which is a separate decision")


if __name__ == "__main__":
    main()
