"""Independent adjudicator: find real redirections by scanning characters and tracking quotes.

The gate's own regex cannot be the ground truth for "was this really a write" — that is the
thing under test. This module parses the command character by character, so a `>` or `|` inside
a quoted span is text and one outside is syntax.

Used by probe_st9_real_false_denies.py.
"""

from __future__ import annotations

from typing import Final

WRITE_VERBS: Final[frozenset[str]] = frozenset(
    {
        "rm", "mv", "cp", "mkdir", "rmdir", "tee", "touch", "truncate", "install",
        "chmod", "chown", "ln", "rsync", "scp", "dd", "shred", "unzip", "cpio", "tar",
    }
)
DISCARD: Final[frozenset[str]] = frozenset({"/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/stdin"})


def outside_quotes(command: str) -> str:
    """The command with quoted spans replaced by spaces.

    Args:
        command: Raw command line.

    Returns:
        Same length as the input, so offsets remain comparable; quoted characters become spaces.
    """
    out: list[str] = []
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            out.append(" " if quote else char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            out.append(" " if quote else char)
            continue
        if quote is None and char in "'\"":
            quote = char
            out.append(" ")
            continue
        if quote is not None and char == quote:
            quote = None
            out.append(" ")
            continue
        out.append(" " if quote is not None else char)
    return "".join(out)


def segments(command: str) -> list[str]:
    """Split on real `;`, `&&`, `||` and `|` separators (outside quotes).

    Args:
        command: Raw command line.

    Returns:
        Command segments, in order.
    """
    bare = outside_quotes(command)
    parts: list[str] = []
    start = 0
    index = 0
    while index < len(bare):
        if bare[index] in ";&|":
            parts.append(command[start:index])
            # A pipeline continues into the next command; a separator starts a new one. Either
            # way the next segment begins after the operator.
            while index < len(bare) and bare[index] in ";&|":
                index += 1
            start = index
            continue
        index += 1
    parts.append(command[start:])
    return [part.strip() for part in parts if part.strip()]


def redirection_targets(command: str) -> list[str]:
    """Paths this command really redirects a file descriptor into, outside quotes.

    Handles the forms a shell actually accepts: ``>``, ``>>``, ``2>``, ``2>&1``, ``&>``,
    ``0<``. A descriptor duplication (``>&1``, ``>&2``, ``>&-``) is NOT a file target, and
    reading it as one produced a phantom target ``1`` in the first version of this probe.

    Args:
        command: Raw command line.

    Returns:
        Target paths, quoting removed; empty when nothing is redirected to a file.
    """
    bare = outside_quotes(command)
    targets: list[str] = []
    index = 0
    while index < len(bare):
        if bare[index] not in "><":
            index += 1
            continue
        cursor = index
        # Consume the operator: >, >>, >&, &>, 0< etc. A leading digit belongs to the operator.
        while cursor < len(bare) and bare[cursor] in "<>&":
            cursor += 1
        while cursor < len(bare) and bare[cursor] in " \t":
            cursor += 1
        # `>&1`, `>&2`, `>&-`: duplication of a descriptor, no file involved.
        if bare[cursor:].startswith(tuple("0123456789-")) and "<" not in bare[index:cursor]:
            index = cursor + 1
            continue
        end = cursor
        while end < len(bare) and bare[end] not in " \t;&|<>":
            end += 1
        if cursor < len(bare):
            raw = command[cursor:end].strip("\"'`")
            if raw and raw not in DISCARD and not raw.startswith("&"):
                targets.append(raw)
        index = max(end, index + 1)
    return targets


def write_verbs(command: str) -> list[str]:
    """Write-verb invocations outside quotes.

    Args:
        command: Raw command line.

    Returns:
        The verbs seen at the start of a segment.
    """
    found: list[str] = []
    for segment in segments(command):
        head = segment.split()
        while head and ("=" in head[0] or head[0] in {"sudo", "env", "command", "time", "nice"}):
            head = head[1:]
        if head and head[0] in WRITE_VERBS:
            found.append(head[0])
        # `git <write-verb>` is a write as well.
        if len(head) > 1 and head[0] == "git" and head[1] in {
            "apply", "checkout", "restore", "reset", "stash", "commit", "add", "rm", "mv",
        }:
            found.append(f"git {head[1]}")
        # `sed -i`, `perl -i`: in-place edits.
        if head and head[0] in {"sed", "perl"} and any(item.startswith("-i") for item in head[1:]):
            found.append(f"{head[0]} -i")
    return found


def really_writes(command: str) -> bool:
    """Whether the command would change the filesystem.

    Args:
        command: Raw command line.

    Returns:
        True when a real redirection or a write verb is present outside quotes.
    """
    if redirection_targets(command):
        return True
    return bool(write_verbs(command))
