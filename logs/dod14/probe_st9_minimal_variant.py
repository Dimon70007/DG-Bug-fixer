"""Minimal variant under review: keep the gate's own regex, apply it to the command with quoted
spans blanked out, then re-scan the contents of a nested `-c` string.

Nothing else changes: same verb list, same /dev exemptions, same target extraction. The question
is only whether removing quoted text from the scan loses a real write.
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
from probe_st9_real_false_denies import commands  # noqa: E402

NESTED_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+|env\s+)*(?:/\S*/)?(?:bash|sh|zsh|dash|ksh)\s+(?:-\w+\s+)*-c\s+(['\"])(.*?)\1",
    re.DOTALL,
)
PY_C_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+|env\s+)*(?:/\S*/)?python3?\s+-c\s+(['\"])(.*?)\1",
    re.DOTALL,
)


def minimal_is_write(command: str) -> bool:
    """Gate regex on the unquoted skeleton, plus a scan of nested `-c` payloads.

    Args:
        command: Raw command line.

    Returns:
        True when the command writes anywhere a shell would act on it.
    """
    if gate._WRITE_COMMAND_RE.search(shell_scan.outside_quotes(command)):
        return True
    for pattern in (NESTED_RE, PY_C_RE):
        for match in pattern.finditer(command):
            inner = match.group(2)
            if gate._WRITE_COMMAND_RE.search(shell_scan.outside_quotes(inner)):
                return True
    return False


CONTROLS = [
    ("echo hi > out.txt", True),
    ("cat a.txt > b.txt", True),
    ("rm -rf build 2>/dev/null", True),
    ("pytest -q 2> errors.log", True),
    ("tee build.log", True),
    ("cp src/a.py tests/b.py", True),
    ("mkdir -p logs/dumps", True),
    ("pytest -q 2>/dev/null", False),
    ('grep -rn "chdir|mkdir" logs/', False),
    ("rg 'x>>y' docs/", False),
    ("python3 -c \"print('a -> b')\"", False),
    ('bash -c "echo hi > f.txt"', True),
    ("bash -c 'cat >> x <<EOF'", True),
    ("sed -i s/a/b/ f.txt", True),
    ("git commit -m 'msg'", True),
    ("git diff --stat", False),
    ("ls -lt tasks/ | head", False),
    ('echo "=== with Test p"', False),
    ('rg "a|mkdir" hooks/', False),
    ("python3 -c \"open('f','w').write('x')\"", True),
]


def main() -> None:
    print("== controls ==")
    wrong = 0
    for command, expected in CONTROLS:
        got = minimal_is_write(command)
        mark = "ok " if got == expected else "WRONG"
        if got != expected:
            wrong += 1
        print(f"  {mark} expected={expected!s:5} got={got!s:5}  {command[:66]}")
    print(f"  wrong: {wrong} of {len(CONTROLS)}")

    every = commands()
    flagged_now = [c for c in every if gate._WRITE_COMMAND_RE.search(c)]
    flagged_new = [c for c in every if minimal_is_write(c)]
    print("\n== session corpus ==")
    print(f"  commands                    : {len(every)}")
    print(f"  gate says write (today)     : {len(flagged_now)}")
    print(f"  minimal rule says write     : {len(flagged_new)}")
    print(f"  difference                  : {len(flagged_new) - len(flagged_now):+d}")

    print("\n== veto: commands the gate catches and the minimal rule does NOT ==")
    missed = [c for c in flagged_now if not minimal_is_write(c)]
    print(f"  {len(missed)} — each must be a read:")
    for command in missed[:25]:
        targets = shell_scan.redirection_targets(command)
        print(f"    targets={targets}  {command[:110]}")

    print("\n== and the reverse: minimal rule catches, gate does not ==")
    extra = [c for c in flagged_new if not gate._WRITE_COMMAND_RE.search(c)]
    print(f"  {len(extra)}")
    for command in extra[:6]:
        print(f"    {command[:110]}")


if __name__ == "__main__":
    main()
