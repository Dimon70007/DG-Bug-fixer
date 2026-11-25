#!/usr/bin/env python3
"""Secret scanner for bug-fixer ledgers and diffs (TASK-081 ST-4a).

Three mechanisms cover three leak points, and none of them is complete: the rule
in ``SKILL.md`` (before the line is written), the agent's own diff scan (step
4.6a), and this gate scan (on every read of a record). Together they catch the
same secret at three different moments.

**Two levels, and the split is not cosmetic.** A value after ``password=`` is a
leak. ``password_present=true`` is the *fact* of presence, which the rule forbids
too — but ``cache_present=true`` is an ordinary flag. Warning on both would make
the gate block harmless code, and a gate that blocks harmless code is removed in
its first week, taking the real checks with it.

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

#: Patterns that refuse: an actual value is present. Kept as strings so a test can
#: check each one independently and name the rule that fired.
#:
#: Order matters: the specific shapes come first, because the generic assignment
#: pattern would otherwise swallow a `Bearer …` header as a name/value pair and the
#: refusal would name the wrong rule.
SECRET_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("openai", r"\bsk-[A-Za-z0-9_-]{16,}"),
    ("github", r"\bghp_[A-Za-z0-9]{20,}"),
    ("aws", r"\bAKIA[0-9A-Z]{16}\b"),
    # A JWT is three dot-separated base64url segments; the spec's pattern required
    # a trailing dot, so a real token never matched it.
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer", r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    (
        "assignment",
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|authorization)\b\s*[:=]\s*\S+",
    ),
    ("base64", r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)

#: Pattern that only warns: it catches the fact of presence, and the fact of
#: presence is usually a flag rather than a secret. ``password_present=true`` and
#: ``password_present=false`` are both facts; only the value is a leak.
ADVISORY_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    (
        "presence",
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|key|credential)s?"
        r"[_-]?(present|exists|found)\s*[:=]\s*(true|false|yes|no)\b",
    ),
)

#: Value shapes that are not secrets even when they follow a secret-looking name.
_NON_SECRET_VALUES: Final[frozenset[str]] = frozenset(
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
    }
)

_REDACT_HEAD: Final[int] = 4


@dataclass(frozen=True)
class Finding:
    """One match.

    Attributes:
        line_no: 1-based line number.
        rule: Pattern name, so a refusal says which rule fired.
        excerpt: The line with the value masked.
        advice_only: ``True`` for the advisory level.
    """

    line_no: int
    rule: str
    excerpt: str
    advice_only: bool


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


def _is_secret_assignment(line: str) -> bool:
    """Whether an assignment-shaped match carries a real value.

    ``password: ${PASS}`` and ``token: null`` are configuration, not leaks.

    Args:
        line: Line containing the match.

    Returns:
        ``True`` when the value looks like a secret.
    """
    match = re.search(r"[:=]\s*(\S+)", line)
    if match is None:
        return False
    value = match.group(1).strip().strip("\"'`,;")
    if value.lower() in _NON_SECRET_VALUES:
        return False
    if value.startswith(("$", "{", "%", "<")):
        return False
    return True


def scan(text: str) -> list[Finding]:
    """Scan text for secret patterns.

    Args:
        text: Content to scan.

    Returns:
        Findings in file order, refusals and advisories together.
    """
    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines(), start=1):
        for name, pattern in SECRET_PATTERNS:
            if not re.search(pattern, line):
                continue
            if name == "assignment" and not _is_secret_assignment(line):
                continue
            findings.append(Finding(index, name, _mask_value(line), advice_only=False))
            break
        else:
            for name, pattern in ADVISORY_PATTERNS:
                if re.search(pattern, line):
                    findings.append(Finding(index, name, line, advice_only=True))
                    break
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    """Filter to the findings that must refuse.

    Args:
        findings: Findings from :func:`scan`.

    Returns:
        Findings that are not advisory.
    """
    return [finding for finding in findings if not finding.advice_only]


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
    for finding in blocking(findings):
        print(describe(finding), file=sys.stderr)
    for finding in findings:
        if finding.advice_only:
            print(f"warning: {describe(finding)}", file=sys.stderr)
    return EXIT_SECRET if blocking(findings) else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
