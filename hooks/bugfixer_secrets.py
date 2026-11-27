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
from typing import Final

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


@dataclass(frozen=True)
class Finding:
    """One match.

    Attributes:
        line_no: 1-based line number.
        rule: Pattern name, so a refusal says which rule fired.
        excerpt: The line with the value masked.
    """

    line_no: int
    rule: str
    excerpt: str


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


def scan(text: str) -> list[Finding]:
    """Scan text for secret patterns.

    Every pattern refuses — there is no advisory level (§10, 2026-11-27). A match is skipped only
    when the assignment turns out to report setup, which is the one statement §10 requires.

    Args:
        text: Content to scan.

    Returns:
        Findings in file order.
    """
    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines(), start=1):
        for name, pattern in SECRET_PATTERNS:
            match = re.search(pattern, line)
            if match is None:
                continue
            if name in {"assignment", "derived"} and _is_setup_report(match.group(0)):
                continue
            findings.append(Finding(index, name, _mask_value(line)))
            break
    return findings


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
