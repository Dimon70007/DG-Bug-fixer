"""Can the veto be closed? The 111 real writes the minimal rule misses are of three shapes:

  1. `git add` — a write verb the gate's regex never listed;
  2. `python3 - <<'PY' … PY` — a heredoc fed to an interpreter: arbitrary code, writes anything;
  3. the same with `bash`/`sh`.

Shape 1 is a pre-existing gap (the gate does not count `git add` at all). Shapes 2 and 3 are
introduced by removing quoted text from the scan, because the heredoc body is the quote.
This probe adds a heredoc rule and recounts.
"""

from __future__ import annotations

import re
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

#: `<interpreter> … <<'PY' … PY`: the body is executed, so it is a command, not text.
HEREDOC_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+|env\s+)*(?:/\S*/)?"
    r"(?:python3?|bash|sh|zsh|perl|ruby|node)\b[^;\n]*<<-?\s*(['\"]?)(\w+)\1",
    re.MULTILINE,
)


def has_heredoc(command: str) -> bool:
    """Whether a heredoc body is fed to an interpreter.

    Args:
        command: Raw command line.

    Returns:
        True when the command runs a program with a heredoc on stdin.
    """
    return bool(HEREDOC_RE.search(command))


def candidate(command: str) -> bool:
    """The minimal rule plus a heredoc veto.

    Args:
        command: Raw command line.

    Returns:
        True when the command writes, treating a heredoc body as executable.
    """
    return minimal_is_write(command) or has_heredoc(command)


def main() -> None:
    every = commands()
    flagged_now = [c for c in every if gate._WRITE_COMMAND_RE.search(c)]
    flagged_new = [c for c in every if candidate(c)]
    print(f"commands                : {len(every)}")
    print(f"gate says write today   : {len(flagged_now)}")
    print(f"candidate rule says write: {len(flagged_new)}")

    dropped = [c for c in flagged_now if not candidate(c)]
    real = [c for c in dropped if shell_scan.really_writes(c)]
    print("\n== veto recount ==")
    print(f"  no longer called a write : {len(dropped)}")
    print(f"  of those, REALLY write   : {len(real)}")
    for command in real:
        print(f"    verbs={shell_scan.write_verbs(command)} targets={shell_scan.redirection_targets(command)}")
        print(f"      {command[:140]}")

    print("\n== controls still correct? ==")
    for command, expected in (
        ("pytest -q 2>/dev/null", False),
        ('grep -rn "chdir|mkdir" logs/', False),
        ("python3 -c \"print('a -> b')\"", False),
        ('bash -c "echo hi > f.txt"', True),
        ("python3 - <<'PY'\nimport pathlib\nPY", True),
        ("echo hi > out.txt", True),
        ("git commit -m 'msg'", True),
    ):
        got = candidate(command)
        print(f"  {'ok ' if got == expected else 'WRONG'} expected={expected!s:5} got={got!s:5} {command[:56]!r}")


if __name__ == "__main__":
    main()
