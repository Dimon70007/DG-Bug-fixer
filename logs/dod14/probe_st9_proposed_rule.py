"""Proposed rule, measured before any edit: a `>` or `|` inside quotes is TEXT, unless the
quoted span is itself a command (`bash -c`, `python -c`, `sh -c` …).

Two numbers decide whether the change is safe:

* how many of the 102 false "write" classifications it fixes;
* how many real writes it would MISS (a bypass) — the only number that can veto it.

Ground truth for "really writes" is a hand-labelled sample, not either scanner.
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

#: Interpreters whose quoted argument is executed as its own command line.
NESTED = ("bash", "sh", "zsh", "dash", "ksh", "su", "runuser")


def has_nested_execution(command: str) -> bool:
    """Whether a segment runs a quoted string as a command.

    Args:
        command: Raw command line.

    Returns:
        True when the command contains `<shell> -c <string>` or `su/runuser … -c <string>`.
    """
    for segment in shell_scan.segments(command):
        words = segment.split()
        while words and (words[0] in {"sudo", "env", "command", "time", "nice"} or "=" in words[0]):
            words = words[1:]
        if not words:
            continue
        base = Path(words[0]).name
        if base in NESTED and any(word == "-c" or word.startswith("-c") for word in words[1:]):
            return True
    return False


def proposed_is_write(command: str) -> bool:
    """The proposed classification.

    Args:
        command: Raw command line.

    Returns:
        True when the command writes: a real redirection or write verb outside quotes; inside a
        nested `-c` string the quoted text is scanned as syntax, because it is.
    """
    if shell_scan.redirection_targets(command):
        return True
    if shell_scan.write_verbs(command):
        return True
    # A nested `-c` string is a command of its own: scan its contents with the same rules.
    if has_nested_execution(command):
        for match in re.finditer(r"-c\s+(['\"])(.*?)\1", command, re.DOTALL):
            inner = match.group(2)
            if shell_scan.redirection_targets(inner) or shell_scan.write_verbs(inner):
                return True
    return False


#: Hand-labelled control set: each entry is (command, really writes?). Read from the gate's own
#: tests, where the author stated the intent in the test name.
def controls() -> list[tuple[str, bool]]:
    return [
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
        ("bash -c \"echo hi > f.txt\"", True),
        ("sh -c \"cat >> x <<EOF\"", True),
        ("sed -i s/a/b/ f.txt", True),
        ("git commit -m 'msg'", True),
        ("git diff --stat", False),
        ("ls -lt tasks/ | head", False),
        ('echo "=== with Test p"', False),
    ]


def main() -> None:
    print("== hand-labelled controls: would the proposed rule be right? ==")
    wrong = 0
    for command, expected in controls():
        got = proposed_is_write(command)
        mark = "ok " if got == expected else "WRONG"
        if got != expected:
            wrong += 1
        print(f"  {mark} expected={expected!s:5} got={got!s:5}  {command[:70]}")
    print(f"  wrong: {wrong} of {len(controls())}")

    print("\n== session corpus ==")
    every = commands()
    gate_flags = [c for c in every if gate._WRITE_COMMAND_RE.search(c)]
    false_now = [c for c in gate_flags if not shell_scan.really_writes(c)]
    still_write = [c for c in every if proposed_is_write(c)]
    print(f"  commands                       : {len(every)}")
    print(f"  gate says write (today)        : {len(gate_flags)}")
    print(f"  of those, not really writes    : {len(false_now)}")
    print(f"  proposed rule says write       : {len(still_write)}")
    print(f"  net fewer 'write' verdicts     : {len(gate_flags) - len(still_write)}")

    print("\n== the veto question: does the proposal MISS any command the gate catches? ==")
    missed = [c for c in gate_flags if not proposed_is_write(c)]
    print(f"  gate flags but proposal does not: {len(missed)}")
    print("  every one of these must be a read. Shown in full for review:")
    for command in missed:
        nested = " [nested -c]" if has_nested_execution(command) else ""
        print(f"    {command[:150]}{nested}")


if __name__ == "__main__":
    main()
