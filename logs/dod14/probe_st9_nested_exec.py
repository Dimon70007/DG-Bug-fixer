"""Does the independent parser miss real writes hidden inside a nested command?

A quote-aware scan of the OUTER command would call `bash -c "echo hi > f.txt"` a read: the `>`
is inside quotes. But that command DOES write — the quotes are the shell's own, and the inner
string is itself a command.

If such cases exist in the session corpus, the "96 false denies" number is too high and the
proposed fix (ignore quoted metacharacters) would open a bypass. This probe measures both.
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
from probe_st9_real_false_denies import commands  # noqa: E402

#: Invocations whose quoted argument is itself a command.
INTERPRETERS = ("bash", "sh", "zsh", "python", "python3", "node", "perl", "ruby", "eval", "source")


def nested_exec(command: str) -> bool:
    """Whether a quoted argument is executed as its own command."""
    head = shell_scan.segments(command)
    for segment in head:
        words = segment.split()
        if not words:
            continue
        for interpreter in INTERPRETERS:
            if words[0].endswith(interpreter) or words[0] == interpreter:
                if any(flag in words[1:3] for flag in ("-c", "-e", "--eval")):
                    return True
        if words[0] in ("su", "runuser") and "-c" in words:
            return True
    return False


def main() -> None:
    print("== sanity: cases where the quoted text IS a command ==")
    samples = [
        'bash -c "echo hi > f.txt"',
        'sh -c "cat >> x <<EOF"',
        'python -c "print(1)"',
        'grep "a|b" f.txt',
        'sed -i s/a/b/ f.txt',
    ]
    for command in samples:
        print(
            f"  {command:30} gate={gate._WRITE_COMMAND_RE.search(command) is not None!s:5} "
            f"outer_scan={shell_scan.really_writes(command)!s:5} nested={nested_exec(command)}"
        )

    print("\n== how often does the session corpus use nested execution? ==")
    all_commands = commands()
    nested = [command for command in all_commands if nested_exec(command)]
    print(f"  commands with a quoted interpreter invocation: {len(nested)} of {len(all_commands)}")
    for command in nested[:5]:
        print(f"    {command[:100]}")

    print("\n== of those, how many would the current gate call a write? ==")
    flagged = [command for command in nested if gate._WRITE_COMMAND_RE.search(command)]
    print(f"  {len(flagged)} — these must stay denied after any fix")

    print("\n== and how many of my 102 'false writes' are actually nested writes? ==")
    false_writes = [
        command
        for command in all_commands
        if gate._WRITE_COMMAND_RE.search(command) and not shell_scan.really_writes(command)
    ]
    sneaky = [command for command in false_writes if nested_exec(command)]
    print(f"  {len(sneaky)} of {len(false_writes)}")
    for command in sneaky[:5]:
        print(f"    {command[:110]}")


if __name__ == "__main__":
    main()
