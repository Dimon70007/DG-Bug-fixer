"""ST-9 probe 22: how often does the write classifier fire on a READ-ONLY command?

F12 says `_WRITE_COMMAND_RE` scans the raw string, so `>` and `|<verb>` inside quotes count as
shell syntax. This probe measures how often that happens on a corpus of realistic commands:
every string literal in the plugin's own tests that starts with a known command name.

Output is a count, so the claim "this is common" becomes "N of M".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

COMMAND_HEADS = (
    "pytest python python3 git rg grep cat echo ls sed bash sh make curl rm cp mv touch "
    "mkdir tee printf find awk sort head tail wc git diff"
).split()

STRING_RE = re.compile(r'"((?:[^"\\]|\\.){6,200})"')


def collect() -> list[str]:
    found: list[str] = []
    for path in sorted((SKILL / "tests").glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in STRING_RE.finditer(text):
            candidate = match.group(1)
            if candidate.split()[0] in COMMAND_HEADS if candidate.split() else False:
                found.append(candidate)
    return sorted(set(found))


def quoted_metacharacters(command: str) -> bool:
    """True when a `>` or `|` sits INSIDE a quoted span of the command."""
    for match in re.finditer(r"'([^']*)'|\"([^\"]*)\"", command):
        span = match.group(1) if match.group(1) is not None else match.group(2)
        if ">" in span or "|" in span:
            return True
    return False


def main() -> None:
    candidates = collect()
    print(f"string literals that look like commands: {len(candidates)}")

    flagged = [cmd for cmd in candidates if gate._WRITE_COMMAND_RE.search(cmd)]
    print(f"classified as WRITE by the gate           : {len(flagged)}")

    inner = [cmd for cmd in flagged if quoted_metacharacters(cmd)]
    print(f"  ...with `>` or `|` inside quotes        : {len(inner)}")
    for cmd in inner[:20]:
        print(f"      {cmd}")

    print("\nthe same strings, if a shell had parsed them (shlex):")
    import shlex

    parsed_ok = 0
    for cmd in inner:
        try:
            shlex.split(cmd)
            parsed_ok += 1
        except ValueError:
            pass
    print(f"  parseable as a real command line: {parsed_ok} of {len(inner)}")


if __name__ == "__main__":
    main()
