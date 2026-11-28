#!/usr/bin/env python3
"""Secret scanner for bug-fixer ledgers and diffs (TASK-081 ST-4a).

Three mechanisms cover three leak points, and none of them is complete: the rule
in ``SKILL.md`` (before the line is written), the agent's own diff scan (step
4.6a), and this gate scan (on every read of a record). Together they catch the
same secret at three different moments.

**One level: every pattern refuses.** A value after ``password=`` is a leak, and so is anything
derived from it that narrows a search or lets a guess be checked offline — the length, the size,
the hash, the prefix. The old design had a second, advisory level for the *fact* of presence; it
was removed on 2026-11-27 because the fact of setup is not a leak at all (the operator has to be
able to read "the key is set"), and warning about it made the one allowed report look suspicious.
The fact of setup is now allowed outright, by name, in :data:`SETUP_VALUES` and
:data:`SETUP_NAME_PARTS`.

Command line, for the agent's own pre-commit pass:

    python3 hooks/bugfixer_secrets.py --diff        # exit 2 when a secret is found
    python3 hooks/bugfixer_secrets.py --text FILE   # exit 2 on the file's content
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Sequence

EXIT_CLEAN: Final[int] = 0
EXIT_SECRET: Final[int] = 2

#: Patterns that refuse. Kept as strings so a test can check each one independently and name the
#: rule that fired.
#:
#: Order matters twice:
#:
#: 1. `derived` before `assignment`, because the generic pattern cannot match `api_key_len=51` at
#:    all: `_` is a word character, so `\b` finds no boundary between `key` and `_len`;
#: 2. the concrete shapes (`sk-`, `ghp_`, …) before both, because `assignment` would otherwise
#:    swallow a `Bearer …` header as a name/value pair and the refusal would name the wrong rule.
SECRET_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("openai", r"\bsk-[A-Za-z0-9_-]{16,}"),
    ("github", r"\bghp_[A-Za-z0-9]{20,}"),
    ("aws", r"\bAKIA[0-9A-Z]{16}\b"),
    # A JWT is three dot-separated base64url segments; the spec's pattern required
    # a trailing dot, so a real token never matched it.
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer", r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # Anything derived from the value. Spec §10 (changed 2026-11-27): a length narrows a
    # brute-force search, a hash is checkable offline, a prefix names the key's format — none of
    # them is needed to answer "is it configured", which is the only thing that may be reported.
    (
        "derived",
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|authorization)[_-]?"
        r"(len|length|size|bytes|sha1|sha256|sha512|md5|hash|digest|prefix|suffix)"
        r"\s*[:=]\s*\S+",
    ),
    (
        "assignment",
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|authorization)\b\s*[:=]\s*\S+",
    ),
    ("base64", r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)

#: Value shapes that are not secrets even when they follow a secret-looking name: they report the
#: fact of setup (or its absence), which §10 allows and requires. `configured` heads the list
#: because it is the form the skill teaches.
SETUP_VALUES: Final[frozenset[str]] = frozenset(
    {
        "",
        "none",
        "null",
        "nil",
        "false",
        "true",
        "***",
        "<redacted>",
        "redacted",
        "required",
        "string",
        "str",
        "int",
        "bool",
        "configured",
        "unconfigured",
        "not-configured",
        "set",
        "unset",
        "enabled",
        "disabled",
        "present",
        "absent",
        "found",
        "missing",
        "yes",
        "no",
        "ok",
    }
)

#: Name fragments that mark a field as reporting setup rather than carrying a value. The fact may
#: be spelled in the name (`api_key_configured=yes`) instead of the value (`api_key: configured`),
#: and both are the same statement.
SETUP_NAME_PARTS: Final[tuple[str, ...]] = (
    "configured",
    "unconfigured",
    "present",
    "exists",
    "found",
    "enabled",
    "defined",
    "missing",
)

_REDACT_HEAD: Final[int] = 4

#: A redaction already written into a field. It has to be recognised: the placeholder itself
#: reads like an assignment to the `assignment` pattern (`Authorization: <redacted:`), so a
#: second masking pass would eat the rule name and leave a field that names a different rule
#: than the one that fired.
REDACTION_RE: Final[re.Pattern[str]] = re.compile(r"<redacted: [a-z0-9-]+>")


@dataclass(frozen=True)
class Finding:
    """One match.

    ``start`` and ``end`` are the match's offsets inside its line, so a caller can replace
    exactly the value and keep everything around it (TASK-086 ST-12). They are positions in a
    command line, not a property of the secret: an offset says nothing about the value that a
    length or a prefix would not.

    Attributes:
        line_no: 1-based line number.
        rule: Pattern name, so a refusal says which rule fired.
        excerpt: The line with the value masked.
        start: Offset of the match's first character in its line.
        end: Offset one past its last character.
    """

    line_no: int
    rule: str
    excerpt: str
    start: int = 0
    end: int = 0


def _mask_value(text: str) -> str:
    """Mask the value part of a match.

    A refusal that quotes the secret is a second copy of the secret, in a place
    people paste into issues.

    Args:
        text: Line containing the match.

    Returns:
        Line with everything after the first ``:`` or ``=`` reduced to its first
        few characters.
    """
    match = re.search(r"[:=]\s*(\S.*)$", text)
    if match is None:
        return text
    value = match.group(1).strip()
    if len(value) <= _REDACT_HEAD:
        return text
    return text[: match.start(1)] + value[:_REDACT_HEAD] + "***"


def _is_setup_report(matched: str) -> bool:
    """Whether the matched assignment reports *whether* a secret is configured.

    Three spellings of the same statement, and all are allowed (§10):

    * the value says it — ``api_key: configured``;
    * the value says it with a flag — ``api_key: configured=false``, ``password_present=true``;
    * the name says it — ``api_key_configured=yes``.

    The check reads the **matched** assignment, not the first one on the line: a log line is
    `[DEBUG] provider=openai api_key: configured`, and looking at the leftmost `=` would judge
    `openai` — a provider name — as if it were the secret's value.

    Args:
        matched: The text the pattern matched, e.g. ``api_key: configured``.

    Returns:
        ``True`` when the assignment reports setup rather than carrying a value.
    """
    separator = re.search(r"[:=]", matched)
    if separator is None:
        return False
    name = matched[: separator.start()]
    value = matched[separator.end() :].strip().strip("\"'`,;")
    # `configured=false` is the word plus a flag: the flag carries the answer, not the secret.
    head = value.split("=", 1)[0].strip()
    if value.lower() in SETUP_VALUES or head.lower() in SETUP_VALUES:
        return True
    if value.startswith(("$", "{", "%", "<")):
        return True
    lowered = name.lower()
    return any(part in lowered for part in SETUP_NAME_PARTS)


def _findings_in(line: str) -> list[Finding]:
    """Every secret match on one line, not only the first.

    Two callers need different answers from the same scan:

    * a refusal needs the first rule that fired — naming one is enough;
    * masking needs **all** of them, because a field that keeps the command must not keep a
      credential. Measured: with one match per line, a `ghp_…` token survived beside a masked
      `sk-…` on the same command line (`probes/st12_target_masking.py`).

    Overlapping matches are dropped, keeping the leftmost-longest rule order: a `Bearer …` header
    already matched by the concrete pattern must not be matched again by the generic one.

    Args:
        line: One line of text.

    Returns:
        Findings in match order, without overlaps.
    """
    spans = [(match.start(), match.end()) for match in REDACTION_RE.finditer(line)]
    found: list[Finding] = []
    for name, pattern in SECRET_PATTERNS:
        for match in re.finditer(pattern, line):
            if _overlaps(match, spans):
                continue
            if name in {"assignment", "derived"} and _is_setup_report(match.group(0)):
                continue
            spans.append((match.start(), match.end()))
            found.append(Finding(1, name, line, match.start(), match.end()))
    return sorted(found, key=lambda finding: finding.start)


def scan(text: str) -> list[Finding]:
    """Scan text for secret patterns.

    Every pattern refuses — there is no advisory level (§10, 2026-11-27). A match is skipped only
    when the assignment turns out to report setup, which is the one statement §10 requires.

    Two other things are skipped, both because a field may already carry them:

    * an existing ``<redacted: rule>`` — the placeholder reads as an assignment to the generic
      pattern, so a second pass would mask the rule name and name the wrong rule;
    * an ordinary path matched by the `base64` rule — done by the caller, which knows the target.

    Each finding carries the offsets of its match, so a caller that has to record the line can
    replace the value and keep the rest of it (TASK-086 ST-12).

    Args:
        text: Content to scan.

    Returns:
        Findings in file order, one per line: the first rule that fired. Masking uses
        :func:`mask`, which takes every match on the line.
    """
    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines(), start=1):
        on_line = _findings_in(line)
        if not on_line:
            continue
        first = on_line[0]
        findings.append(
            Finding(index, first.rule, _mask_value(line), first.start, first.end)
        )
    return findings


def _overlaps(match: re.Match[str], spans: Sequence[tuple[int, int]]) -> bool:
    """Whether a candidate match touches an existing ``<redacted: rule>``.

    This is what keeps a second masking pass from renaming the first rule: one pass leaves
    ``Authorization: <redacted: bearer>``, the generic `assignment` pattern reads the placeholder
    as its value, and masking it again would say `<redacted: assignment>` — a rule that never
    fired, the exact mislabelling TASK-086 ST-12 removes. The check is overlap, not "starts
    inside", because the match begins at the name and runs into the placeholder.

    Args:
        match: Candidate match.
        spans: Offsets of the placeholders already on the line.

    Returns:
        ``True`` when the two share any character.
    """
    return any(match.start() < end and start < match.end() for start, end in spans)


def mask(
    text: str, *, keep: Callable[[Finding, str], bool] | None = None
) -> str:
    """Replace each secret match with its rule name, keeping the rest of the text.

    §10 forbids the **value**; it never asked for the line around it. A journal field that keeps
    the command and drops one argument still answers "what was attempted", while a field replaced
    whole answers nothing (TASK-086 ST-12, measured: a four-argument command recorded as
    ``<redacted: assignment>``).

    Only the matched span goes, so what a reader loses is exactly what the rule objected to, and
    the scan covers **every** match rather than the first one on each line: masking one per line
    left a second credential in clear (`probes/st12_target_masking.py`, case
    `two-secrets-one-line`).

    Args:
        text: The text to mask.
        keep: Called as ``keep(finding, text)`` for each match; ``True`` means "record as is" —
            the caller's exemption for an ordinary path matched by the `base64` rule, which only
            the caller can judge.

    Returns:
        The text with every match replaced by ``<redacted: rule>``, except those ``keep`` allows.
    """
    lines = text.splitlines(keepends=True)
    by_line: dict[int, list[Finding]] = {}
    for index, line in enumerate(lines, start=1):
        for finding in _findings_in(line.rstrip("\r\n")):
            if keep is not None and keep(finding, line.rstrip("\r\n")):
                continue
            by_line.setdefault(index, []).append(finding)
    for line_no, group in by_line.items():
        index = line_no - 1
        line = lines[index]
        # Right to left: an earlier replacement must not shift a later offset.
        for finding in sorted(group, key=lambda item: item.start, reverse=True):
            line = f"{line[: finding.start]}<redacted: {finding.rule}>{line[finding.end :]}"
        lines[index] = line
    return "".join(lines)


def describe(finding: Finding) -> str:
    """Render a finding for a refusal message.

    Args:
        finding: The finding.

    Returns:
        One line naming the rule, the line number, and the masked excerpt.
    """
    return f"line {finding.line_no} ({finding.rule}): {finding.excerpt}"


def _diff_text() -> str:
    """Read the working-tree diff.

    Returns:
        The diff, or empty when git is unavailable.
    """
    result = subprocess.run(
        ["git", "diff", "HEAD", "--unified=0"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` when clean, ``2`` when a secret is found.
    """
    parser = argparse.ArgumentParser(
        prog="bugfixer_secrets.py",
        description="Scan a diff or a file for secrets before they reach the ledger.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diff", action="store_true", help="scan the working-tree diff")
    group.add_argument("--text", help="scan one file")
    args = parser.parse_args(argv)

    if args.diff:
        text = _diff_text()
    else:
        path = Path(args.text).expanduser()
        if not path.is_file():
            print(f"bugfixer-secrets: no such file: {path}", file=sys.stderr)
            return EXIT_SECRET
        text = path.read_text(encoding="utf-8", errors="replace")

    findings = scan(text)
    for finding in findings:
        print(describe(finding), file=sys.stderr)
    return EXIT_SECRET if findings else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
