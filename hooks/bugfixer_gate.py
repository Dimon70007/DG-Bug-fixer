#!/usr/bin/env python3
"""Gate that enforces the bug-fixer loop from an agent hook (TASK-079).

An agent harness runs this before a tool call, passing its own event payload on
stdin. The gate decides whether the call is allowed under the bug-fixer rules:

* the iteration cap ``N >= 10`` (counted from the ledger's ``## Iteration N``
  headings, because a hook sees tool calls, not loops);
* no writes once the ledger is ``BLOCKED`` or ``DONE``;
* no writes to non-test files in Phase 1.

Verdict on stdout as JSON, and in the exit code: ``0`` allow, ``2`` deny. Every
adapter maps that pair onto its harness.

The gate fails **closed** for writes when its own state is unreadable, and
**open** when no bug-fixer run is active, because a gate that denies every tool
call in every repository gets uninstalled within a day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, NoReturn

# The hooks directory is not a package: the gate is executed by path from a
# harness, so its own directory must be importable for the sibling scanner.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bugfixer_journal as journal
import bugfixer_secrets as secrets

# The contract schema lives in ``lib/``, one level up from this module. The gate is executed by
# path from a harness, so ``lib`` has to be importable explicitly; reading the marker's meaning
# from the same module that writes it is what keeps the two in step.
_LIB = _HERE.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from bugfixer import contract as contract_module  # noqa: E402

#: Exit codes shared with every adapter.
EXIT_ALLOW: Final[int] = 0
EXIT_DENY: Final[int] = 2


def _cli_path() -> str:
    """Absolute path of the plugin's single entry point.

    Refusals name the command the agent should run next, and the agent's working directory is the
    project — so a bare `bugfixer` is only correct when the plugin's `bin/` happens to be on
    `PATH`, which a plugin install does not guarantee. The path is derived from this file, not from
    the environment, so the same message is right on every machine.

    Returns:
        Absolute path to ``bin/bugfixer`` inside this plugin tree.
    """
    return str(Path(__file__).resolve().parents[1] / "bin" / "bugfixer")


#: Probes that reached a code change (``Attempt <k> consumed``).
MAX_ATTEMPTS: Final[int] = 10

#: Re-runs of one hypothesis (``Recheck H<n> #<k>``).
MAX_RECHECKS: Final[int] = 5

#: Hypotheses per task — equal to :data:`MAX_ATTEMPTS`: each probe costs a code change.
MAX_HYPOTHESES: Final[int] = 10

#: Marker-line prefix: list/quote/emphasis marks, but deliberately **not** ``|``,
#: so a table row cannot satisfy a marker (invariant 17b).
_PREFIX: Final[str] = r"^[ \t>*_-]{0,8}"

#: ``Attempt <k> consumed`` — one probe that reached a code change.
_ATTEMPT_RE: Final[re.Pattern[str]] = re.compile(_PREFIX + r"Attempt\s+(\d+)\s+consumed\b", re.MULTILINE)

#: ``Recheck H<n> #<k>`` — one re-run of a broken target test.
_RECHECK_RE: Final[re.Pattern[str]] = re.compile(_PREFIX + r"Recheck\s+(H\d+)\s+#(\d+)\b", re.MULTILINE)

#: Counter reset. Only the operator may write it, and the reason is mandatory.
_COUNTER_RESET_RE: Final[re.Pattern[str]] = re.compile(
    _PREFIX + r"Counter reset:\s*A\s*->\s*0\s*\(by operator([^)]*)\)", re.MULTILINE
)

#: Any counter-reset line, to catch a reset that failed the strict form above.
_COUNTER_RESET_ANY_RE: Final[re.Pattern[str]] = re.compile(_PREFIX + r"Counter reset:", re.MULTILINE)

#: ``### H<n> — assertion`` — en dash, as everywhere in the project.
_HYPOTHESIS_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^###\s+(H\d+)\s+—\s+(.+)$", re.MULTILINE)

#: ``### F<n> — title``.
_RECORD_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^###\s+(F\d+)\s+—\s+(.+)$", re.MULTILINE)

#: ``Working on: H<n>`` — the active-hypothesis pointer.
_WORKING_ON_RE: Final[re.Pattern[str]] = re.compile(_PREFIX + r"Working on:\s*(H\d+)\s*$", re.MULTILINE)

#: Start of any level-2/3 heading, used to bound a block's body.
_BLOCK_END_RE: Final[re.Pattern[str]] = re.compile(r"^#{2,3}\s", re.MULTILINE)

#: A ``| Field | Value |`` row inside a block table.
_TABLE_ROW_RE: Final[re.Pattern[str]] = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)

#: A ``#### Records`` bullet: ``- F3 → REFUTED``.
_RECORD_LINK_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*[-*][ \t]+(F\d+)[ \t]*→[ \t]*([A-Z][A-Z ]*)[ \t]*(?:—[ \t]*(.*))?$", re.MULTILINE
)

#: Table-form attempt, e.g. ``| Attempt | 3 consumed |`` — a format error, not a marker.
_TABLE_ATTEMPT_RE: Final[re.Pattern[str]] = re.compile(r"^\|.*\bAttempt\b.*\bconsumed\b", re.MULTILINE)

#: Table-form recheck, e.g. ``| Recheck | H1 #1 |``.
_TABLE_RECHECK_RE: Final[re.Pattern[str]] = re.compile(r"^\|.*\bRecheck\b.*#\d+", re.MULTILINE)

#: Reasons `BLOCKED` may carry (spec §9). A closed list, so `Причина: because` is not a reason.
BLOCKED_REASONS: Final[tuple[str, ...]] = (
    "no-reproduction",
    "baseline-wrong",
    "metrics-conflict",
    "contradictory-proof",
    "operator-needed",
)

#: ``Причина: <value>`` — the field that makes `BLOCKED` actionable.
_BLOCKED_REASON_RE: Final[re.Pattern[str]] = re.compile(
    _PREFIX + r"Причина:[ \t]*([^\n]*)", re.MULTILINE
)

#: ``Логи: <path or lines>`` — the other half of `BLOCKED`.
_LOGS_RE: Final[re.Pattern[str]] = re.compile(_PREFIX + r"Логи:[ \t]*(\S[^\n]*)", re.MULTILINE)

#: The ``## Report`` section (invariant 19). The spec's marker table writes it as
#: ``## Report (поля §7)``, so a parenthetical suffix is allowed: the heading name is what
#: matters, and an agent transcribing the table must not be refused for the annotation.
_REPORT_RE: Final[re.Pattern[str]] = re.compile(r"^##[ \t]+Report(?:[ \t]*\([^)]*\))?[ \t]*$", re.MULTILINE)

#: The ``## Remaining`` section (invariant 28). The spec's marker table writes it as
#: ``## Remaining: <список>``, where ``<список>`` is the placeholder for the handover list;
#: a heading carrying that placeholder, or a parenthetical, is still the section.
_REMAINING_RE: Final[re.Pattern[str]] = re.compile(
    r"^##[ \t]+Remaining(?:[ \t]*:[^\n]*|[ \t]*\([^)]*\))?[ \t]*$", re.MULTILINE
)

#: One item under ``## Remaining``: a bullet, a numbered item, or a table row. §9 asks each
#: item to state what is left, why, how it is verified and who closes it — a shape that a
#: table carries as well as a list, and this repository's own PARTIAL ledger uses a table.
_REMAINING_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+\S|\d+[.)][ \t]+\S|\|[^|\n]+\|)", re.MULTILINE
)

#: A table header rule (``|---|---|``) is structure, not an item: it must not satisfy the
#: "the remainder lists something" check on its own.
_REMAINING_TABLE_RULE_RE: Final[re.Pattern[str]] = re.compile(r"^[ \t]*\|[\s:|-]+\|[ \t]*$")

#: The task block: everything from ``## Task`` to the next level-2 heading.
_TASK_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"^##[ \t]+Task[ \t]*$(.*?)(?=^##[ \t]|\Z)", re.MULTILINE | re.DOTALL
)

#: Fingerprint length for the task block. Short on purpose: it detects a rewrite,
#: it is not a security boundary, and a long hex string invites copy-paste fixes.
_TASK_FINGERPRINT_CHARS: Final[int] = 16

#: Ledger heading that only 2.x wrote. ``Iteration`` was the pass marker; 3.0 records
#: the pass in ``| Iteration | <n> |`` of a block instead.
_LEGACY_ITERATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^#{1,4}\s*Iteration\s+\d+\b", re.MULTILINE
)

#: The 2.x finding header. 3.0 numbers records the same way, so this alone proves nothing.
_LEGACY_FINDING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,4}\s*F\d+\b", re.MULTILINE)

#: Verdicts a ``#### Records`` bullet may carry. ``PARTIALLY``/``DEGRADED`` keep the
#: hypothesis unchecked: it is deepened, not retired (§5.1).
_UNCHECKED_VERDICTS: Final[frozenset[str]] = frozenset({"PROPOSED", "PARTIALLY", "DEGRADED"})

#: Shell constructs that write to disk. Deliberately conservative: a false deny
#: is noisy, a false allow is a silent bypass.
#:
#: A redirection into a *device* is excluded, because it is how every command silences itself:
#: `pytest -q 2>/dev/null` writes no file. Measured defect (2026-11-27): that command was
#: classified as a production write and refused in phase 1 — the phase whose whole job is to run
#: the suite and record a baseline. The discard targets are `/dev/null`, `/dev/zero`, `/dev/std*`
#: and a closing `>&-`; anything else after `>` or `>>` is still a write.
_DISCARD_TARGET_RE: Final[str] = r"/dev/(?:null|zero|std(?:in|out|err))\b|[>&]-"

#: Reading-only invocations of the archivers, limited to the unambiguous long flags. A bare
#: `tar tzf` cluster is not distinguished: treating a listing as a write is a false deny, which
#: is noisy but safe; treating a write as a listing is a bypass, which is not. Only long flags
#: are excluded, because `--list`/`--version` cannot be confused with `xf`/`czf`.
_ARCHIVE_READ_FLAGS: Final[str] = r"(?!-{1,2}(?:--list|--version|--help|--usage)\b)"

_WRITE_COMMAND_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    rf"tar\s+{_ARCHIVE_READ_FLAGS}|unzip\s+(?!-l\b)|cpio\s+(?!-t\b)|rsync|scp|"
    r"rm|mv|cp|install|tee|touch|mkdir|rmdir|truncate|sed\s+-i|perl\s+-i|"
    r"dd\s+of=|chmod|chown|ln\s+-s|git\s+(?:apply|checkout|restore|reset|stash|commit)"
    r")\b"
    rf"|>>?\s*(?!(?:{_DISCARD_TARGET_RE}))[^&|\s]"
)

#: Programs whose quoted argument is a command line of its own: `bash -c '…'`, `python3 -c
#: '…'`. The quotes belong to the shell, but their contents are executed, so the text inside
#: them is syntax and must be scanned. Matched on the basename, because a real session runs
#: `.venv/bin/python` and `/usr/bin/python3` far more often than a bare `python3` (measured:
#: 85 of 169 interpreter calls in one session name a path).
_INTERPRETER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+|env\s+)*"
    r"(?:[^\s;&|]*/)?(?:bash|sh|zsh|dash|ksh|python[0-9.]*|perl|ruby|node)\b"
    r"(?:[^\s;&|]*\s)*?(?:^|\s)-[a-z]*c\b"
)

#: A quoted span, single or double, with escapes inside double quotes honoured.
_QUOTED_SPAN_RE: Final[re.Pattern[str]] = re.compile(r"'([^']*)'|\"((?:[^\"\\]|\\.)*)\"")

#: `<<'X'` or `<<-X`: a heredoc whose body is fed to whatever the segment runs.
_HEREDOC_RE: Final[re.Pattern[str]] = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")

#: Interpreter names whose heredoc body is executed as a program.
_HEREDOC_EXECUTORS: Final[frozenset[str]] = frozenset(
    {"python", "python3", "bash", "sh", "zsh", "ksh", "perl", "ruby", "node"}
)


def _syntax_only(command: str) -> str:
    """The command with quoted spans blanked out.

    Shell metacharacters inside quotes are text, not syntax: `grep "a|mkdir"` searches for
    five characters, it does not pipe into `mkdir`. Scanning the raw string classified that
    as a production write — measured at 96 false refusals in 3312 real commands (2.9%), all
    of them read-only (TASK-086 F12/F22).

    Args:
        command: Raw command line.

    Returns:
        The same string with every quoted span replaced by spaces. Length and offsets are
        preserved, so callers can still slice the original by a match position.
    """
    return _QUOTED_SPAN_RE.sub(lambda match: " " * len(match.group(0)), command)


def _executed_quoted_spans(command: str) -> list[str]:
    """Quoted arguments that are themselves command lines.

    `bash -c 'echo hi > f.txt'` writes a file, even though the `>` sits inside quotes: the
    quotes are the shell's way of passing one argument, and the content of that argument is
    executed. Blanking quotes without this step would open a bypass for every nested call.

    Args:
        command: Raw command line.

    Returns:
        The contents of each `-c` argument, in command order.
    """
    spans: list[str] = []
    for match in _INTERPRETER_RE.finditer(command):
        # The argument follows the `-c` flag; find the next quoted span after the match end.
        tail = command[match.end() :]
        quoted = _QUOTED_SPAN_RE.match(tail.lstrip()) if tail.strip() else None
        if quoted is not None:
            spans.append(quoted.group(1) if quoted.group(1) is not None else quoted.group(2))
    return spans


def _heredoc_bodies_are_executed(command: str) -> bool:
    """Whether a heredoc body is fed to an interpreter, so the body is a program.

    `python3 - <<'PY' … PY` and `bash <<'SH' … SH` run the body. A heredoc into `cat` is
    data. Measured: this gate used to treat a heredoc into an interpreter as a read, so a
    script that writes files passed the phase ban (TASK-086 F23).

    The interpreter may sit inside a loop — `for f in …; do python3 - "$f" <<'PY' … PY; done`
    — so the check is on the whole command, not on a single segment: searching only the
    segments split on `;` looked at `do python3 …` as one word and missed it.

    Args:
        command: Raw command line.

    Returns:
        ``True`` when an interpreter is invoked anywhere in the command and a heredoc is
        present.
    """
    if not _HEREDOC_RE.search(command):
        return False
    for match in re.finditer(r"(?:[^\s;&|]*/)?([a-z0-9_.-]+)", _syntax_only(command)):
        if match.group(1) in _HEREDOC_EXECUTORS:
            return True
    return False


def _heredoc_body_summary(lines: list[str]) -> str:
    """The one-line stand-in for a heredoc body.

    A body is data, not a call: a 10 MB file passed to a command says nothing about *what* was
    invoked, while a journal is meant to answer exactly that. The call line stays verbatim and
    the body becomes a size — enough to tell a one-line stub from a whole source file, without
    turning the journal into a copy of the data (TASK-086 ST-10).

    Args:
        lines: Body lines, delimiters excluded.

    Returns:
        ``[body: N lines, M bytes]``. The size counts the trailing newline heredoc hands to the
        command, so it equals what the command receives; the count is UTF-8, not characters.
    """
    text = "".join(f"{line}\n" for line in lines)
    return f"[body: {len(lines)} lines, {len(text.encode('utf-8'))} bytes]"


def _heredoc_delimiter(line: str) -> tuple[str, bool] | None:
    """The heredoc opened on a line of the call part, if there is one.

    `<<` inside quotes is text, not an operator: `grep "x << y"` searches for a string. The
    delimiter name, on the other hand, is usually quoted (`<<'EOF'`), so the operator is found in
    the raw line and then rejected when its position falls inside a quoted span.

    Args:
        line: One line of the command.

    Returns:
        ``(terminator, strips_tabs)`` for `<<NAME` / `<<-NAME`, or ``None``. ``strips_tabs``
        follows the `<<-` form, where the terminator may be indented.
    """
    for match in _HEREDOC_RE.finditer(line):
        quoted_spans = [
            (span.start(), span.end()) for span in _QUOTED_SPAN_RE.finditer(line)
        ]
        if any(start <= match.start() < end for start, end in quoted_spans):
            continue
        return match.group(2), match.group(0).startswith("<<-")
    return None


def _command_without_heredoc_bodies(command: str) -> str:
    """The command with every heredoc body replaced by its size summary.

    The delimiter lines stay: they say a heredoc is there and what it was called, which is part
    of the call. Only the text between them is a payload.

    A heredoc whose terminator never arrives is left alone rather than guessed at: an unclosed
    body is not a shape the journal can describe honestly, and truncating it would be the same
    silent answer this rule removes.

    Args:
        command: Raw command line.

    Returns:
        The command line with bodies summarised, or unchanged when it carries none.
    """
    lines = command.split("\n")
    if len(lines) == 1:
        return command
    rebuilt: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        rebuilt.append(line)
        index += 1
        # One heredoc per line of the call part: `cat <<A <<B` is rare, and two passes would need
        # ordering the gate has no reason to model.
        opener = _heredoc_delimiter(line)
        if opener is None:
            continue
        terminator, strips_tabs = opener
        body: list[str] = []
        while index < len(lines):
            candidate = lines[index].lstrip("\t") if strips_tabs else lines[index]
            if candidate == terminator:
                break
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            # Unterminated: the rest was never a body, so put it back and stop summarising.
            rebuilt.extend(body)
            break
        rebuilt.append(_heredoc_body_summary(body))
        rebuilt.append(lines[index])
        index += 1
    return "\n".join(rebuilt)


#: Programs that run another program, so the interpreter is not the first token of the segment:
#: `poetry run python -c …`, `uv run python -c …`, `env python -c …`. Measured (TASK-086 ST-11):
#: 26 real commands in this repository spell it with `poetry run`, and the gate missed every one.
_WRAPPER_RUNNERS: Final[str] = (
    r"(?:sudo|env|poetry\s+run|uv\s+run|time|nice|nohup|exec|command|xargs)\s+"
)

#: Shell commands that set up the environment, matched as whole forms.
#:
#: The window exists so the environment can be prepared during Phase 0a (invariant 37, §5.6), and
#: the operator's ruling of 2026-11-28 14:20 sets its scope: **everywhere environment setup is
#: allowed, installing a dependency is allowed too.** They are one act — `poetry add x` and a
#: hand-edited `pyproject.toml` change the same file — so a window that opened only the linter
#: would be inconsistent with the manifest edit it already permitted.
#:
#: It still lists what is **allowed** rather than what is refused. `poetry run` is 457 of the 472
#: package-manager segments in this repository's transcripts; a "block package managers" rule would
#: break the main way a script is started.
_ENV_SETUP_WINDOW_RE: Final[tuple[re.Pattern[str], ...]] = (
    # Installing dependencies. Any package, any group: that is the ruling.
    re.compile(r"^poetry add\b[^;&|]*$"),
    re.compile(r"^poetry install\b[^;&|]*$"),
    re.compile(r"^poetry lock(?:\s+--no-update)?$"),
    re.compile(r"^(?:python[0-9.]* -m )?pip[0-9.]* install\b[^;&|]*$"),
    # Checking with the linter, which §5.6 requires before a script runs.
    re.compile(r"^(?:python[0-9.]* -m )?ruff --version$"),
    re.compile(r"^ruff check [^\s;&|]+$"),
    re.compile(r"^shellcheck --version$"),
    re.compile(r"^shellcheck [^\s;&|]+$"),
)

#: Files whose content configures the environment. `pyproject.toml` counts only when the write
#: carries a tool table — a dependency list or a linter table — rather than being an arbitrary
#: edit to the file.
_ENV_CONFIG_FILES: Final[frozenset[str]] = frozenset({"ruff.toml", ".ruff.toml"})

#: Tool tables in `pyproject.toml` that count as environment configuration.
_ENV_CONFIG_TABLES: Final[tuple[str, ...]] = ("[tool.ruff", "[tool.poetry")

def _is_env_setup_window(tool: str, args: dict[str, Any]) -> bool:
    """Whether this call is environment setup the init phase may perform (invariant 37, §5.6).

    Two kinds:

    * a **command** that installs a dependency, locks, or runs the linter check, matched as a whole
      form — one command, no chaining, no `sudo`;
    * a **write** of environment configuration — `ruff.toml`, or a `pyproject.toml` whose new
      content carries a tool table.

    The scope is the operator's ruling: where environment setup is allowed, installing a
    dependency is allowed. Narrowing this to the linter alone was inconsistent with the permitted
    `pyproject.toml` edit, which changes the same file by the same amount.

    Args:
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        ``True`` when the call is part of environment setup.
    """
    if tool in _SHELL_TOOLS:
        command = _shell_command(args).strip()
        if not command or "\n" in command:
            # A multi-line command is not one of these forms; refusing to parse it keeps the
            # window from covering `poetry add x\nrm -rf src`.
            return False
        return any(pattern.match(command) for pattern in _ENV_SETUP_WINDOW_RE)

    path = _target_path(args)
    if not path:
        return False
    name = Path(path).name
    if name in _ENV_CONFIG_FILES:
        return True
    if name == "pyproject.toml":
        content = args.get("content") or args.get("new_string") or args.get("newStr") or ""
        return isinstance(content, str) and any(
            table in content for table in _ENV_CONFIG_TABLES
        )
    return False


def _is_inline_script(command: str) -> bool:
    """Whether the command carries its program in the command line (§5.6, invariant 51).

    Two forms, both refused regardless of what the body does:

    * a heredoc fed to an interpreter — `python3 - <<'PY' … PY`;
    * a `-c` argument — `python3 -c "…"`, `bash -c '…'`, and the same behind a wrapper such as
      `poetry run` or `env`.

    The content is deliberately not examined: the gate cannot read a program, so "this `-c` only
    prints" is a claim it cannot check. Saying so is honest; guessing would be the defect.
    `python3 script.py` is a call to a file and is not refused — that is the required form.

    The wrapper form is checked here rather than by extending `_INTERPRETER_RE`: that pattern
    also decides what counts as a *write* (§5.5), and widening it there would change the
    classification of every shell call — a different rule with its own evidence.

    Args:
        command: Raw command line.

    Returns:
        ``True`` when the command inlines its program.
    """
    if _heredoc_bodies_are_executed(command) or _executed_quoted_spans(command):
        return True
    for line in command.splitlines():
        for match in re.finditer(
            rf"(?:{_WRAPPER_RUNNERS})?(?:[^\s;&|]*/)?"
            r"(?:bash|sh|zsh|dash|ksh|python[0-9.]*|perl|ruby|node)\b",
            line,
        ):
            for token in line[match.end() :].split():
                stripped = token.strip("\"'")
                if not stripped.startswith("-"):
                    break
                if "c" in stripped.lstrip("-"):
                    return True
    return False


def _is_new_script_target(target: str, ledger: Path) -> bool:
    """Whether a write creates a script in a place the ban's exit names (§5.6).

    A *new* file under `scripts/` or `probes/` is the alternative to an inline command, so it is
    allowed in every phase. An **existing** file there is a shipped tool — in this repository
    `scripts/linux/*.py` is tracked and driven by the Makefile — and editing it is a code change,
    judged by the phase like any other. Existence is the boundary because it is checkable
    without reading git (§12).

    Args:
        target: Path from the write.
        ledger: Ledger being enforced.

    Returns:
        ``True`` when the target is a script file that does not exist yet.
    """
    cleaned = target.strip().strip("\"'`")
    if not cleaned:
        return False
    candidate = Path(cleaned)
    if not candidate.is_absolute():
        candidate = _project_of(ledger) / candidate
    resolved = _normalized(candidate)
    root = _project_of(ledger)
    if not any(resolved == root / name or root / name in resolved.parents
               for name in ("scripts", "probes")):
        return False
    return not resolved.exists()


def _inline_script_refusal() -> str:
    """The refusal for an inline script, with the exit spelled out (invariant 51).

    A ban with no exit is a wall, and a wall sends the agent back to the same heredoc with a
    different quoting trick. So the text names the file, the linter check and the order, and it
    says plainly that the answer does not change with the phase.

    Returns:
        The refusal message, shared by both places that refuse this form.
    """
    return (
        "inline scripts are refused in every phase: the gate does not read the program, so it "
        "cannot tell `print('a > b')` from `open('f','w')`, and a guess either way is wrong. "
        "This is not a phase rule — the same command is refused in phase 4.\n\n"
        "Write the script to a file, check it, then run it by name:\n"
        "  scripts/<name>.py                          (or probes/<name>.py for a one-off)\n"
        "  ruff --version                             (missing? poetry add --group dev ruff, "
        "or python3 -m pip install ruff)\n"
        "  python3 -m py_compile scripts/<name>.py    (or ruff check; for *.sh: shellcheck, "
        "or bash -n)\n"
        "  python3 scripts/<name>.py\n\n"
        "A NEW file under scripts/ or probes/ may be written in any phase; editing an existing "
        "script is a code change, judged by the phase (invariants 47, 51; spec §5.6)."
    )


#: Package managers and the verbs that write the manifest or the lockfile (F29).
#:
#: The gate classified none of these as a write, so `poetry add requests` passed in every phase and
#: a lockfile could be rewritten with no hypothesis (measured: 12 mutating segments in 601, 0 seen
#: as writes). The rule is on the (program, verb) pair, not on the program name, because
#: `poetry run` is the main way to start a script here — 457 of 472 reading segments.
#:
#: `--dry-run` makes an install a read. Poetry documents it for `install`/`add`/`remove`/`update`
#: (checked against python-poetry.org/docs/cli on 2026-11-28, since poetry is not installed on this
#: machine); `lock` has no dry-run, and `--no-update` still rewrites the lockfile.
_MANAGER_WRITES: Final[tuple[tuple[re.Pattern[str], frozenset[str]], ...]] = (
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?(?:python[0-9.]*\s+-m\s+)?poetry\b"),
     frozenset({"add", "remove", "install", "update", "lock"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?(?:python[0-9.]*\s+-m\s+)?pip[0-9.]*\b"),
     frozenset({"install", "uninstall"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?(?:npm|pnpm|yarn)\b"),
     frozenset({"install", "i", "add", "remove", "uninstall", "un", "update", "up", "ci"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?cargo\b"),
     frozenset({"add", "remove", "update", "install"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?go\b"),
     frozenset({"get", "install"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?gem\b"),
     frozenset({"install", "uninstall", "update"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?composer\b"),
     frozenset({"require", "install", "update", "remove"})),
    (re.compile(r"(?:^|[\s;&|(`])(?:[^\s;&|]*/)?uv\b"),
     frozenset({"add", "remove", "sync", "install", "pip"})),
)

#: A flag that turns an install into a report of what would happen.
_DRY_RUN_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)--dry-run\b")

def _is_package_manager_write(segment: str) -> bool:
    """Whether one shell segment mutates a manifest or a lockfile (F29).

    Args:
        segment: One shell segment, quotes already blanked by the caller.

    Returns:
        ``True`` when a package manager is invoked with a mutating verb and no `--dry-run`.
    """
    if _DRY_RUN_RE.search(segment):
        return False
    for program, verbs in _MANAGER_WRITES:
        match = program.search(segment)
        if match is None:
            continue
        rest = segment[match.end() :].strip()
        tokens = rest.split()
        if not tokens:
            return False
        verb = tokens[0].lower()
        if verb in verbs:
            return True
        # `go mod tidy` is two words; the pair is the verb.
        if verb == "mod" and len(tokens) > 1 and tokens[1].lower() == "tidy":
            return True
    return False


def _is_write_command(command: str) -> bool:
    """Whether a shell command would change the filesystem.

    Reads only the part a shell acts on: text inside quotes is ignored, except where the
    quotes are the shell's way of passing a command ({@link _executed_quoted_spans}) or a
    heredoc body that an interpreter runs ({@link _heredoc_bodies_are_executed}).

    Args:
        command: Raw command line.

    Returns:
        ``True`` when the command writes.
    """
    if _WRITE_COMMAND_RE.search(_syntax_only(command)):
        return True
    # F29: a package manager invoked with a mutating verb writes `pyproject.toml` or a lockfile.
    # Checked per segment, because `cd x && poetry add y` is one command line and only the second
    # segment writes.
    for segment in re.split(r"[;&|\n]+", _syntax_only(command)):
        if _is_package_manager_write(segment):
            return True
    for span in _executed_quoted_spans(command):
        if _WRITE_COMMAND_RE.search(_syntax_only(span)):
            return True
    return _heredoc_bodies_are_executed(command)

#: The destination of a shell redirection, as a path. Discard targets are excluded for the same
#: reason: `/dev/null` is not a file the phase ban is about.
_REDIRECT_TARGET_RE: Final[re.Pattern[str]] = re.compile(
    rf">>?[ \t]*(?!(?:{_DISCARD_TARGET_RE}))([^&|\s;]+)"
)

#: The arguments of a shell command that writes to named paths. The path is what the
#: phase ban is about, and taking it from the command is how a write to the ledger can be
#: told from a write to production code in the same line.
#:
#: Measured gap (2026-11-27): `rsync -a src/ scratch/` and `tar xf a.tgz -C scratch` write files
#: and were classified as reads, because neither name appeared here. That is a bypass of every
#: phase rule, not a cosmetic omission, so the archivers and copy tools come first in the list.
_WRITE_TARGET_COMMANDS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[;&|]\s*)(?:rsync|tar|unzip|cpio|scp|install|mv|cp|rm|tee|touch|mkdir|rmdir|truncate"
    r"|sed\s+-i|perl\s+-i|dd|chmod|chown|ln\s+-s"
    r"|git\s+(?:apply|checkout|restore|reset|stash|commit))\b"
    r"([^\n;&|]*)"
)

#: Harness tool names that write files, lowercased per harness.
_WRITE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "write",
        "edit",
        "multiedit",
        "strreplace",
        "notebookedit",
        "applypatch",
        "writefile",
        "editfile",
        "search_replace",
        "insert_edit_into_file",
    }
)

#: Harness tool names that run shell commands.
_SHELL_TOOLS: Final[frozenset[str]] = frozenset({"bash", "shell", "exec", "run_terminal_cmd", "terminal"})

#: Tools that provably cannot mutate the filesystem. Anything outside all three sets is
#: *unknown*, and unknown is not silently allowed: during an active run it fails closed, because
#: a harness renaming a tool must not turn the gate into decoration. Adding a name here is a
#: deliberate act, and each name in this list came from a captured payload or a documented event —
#: the list is a classification of what the harness can send, not a guess about this project.
_SAFE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "read",
        "readfile",
        "readlints",
        "grep",
        "search",
        "glob",
        "list",
        "ls",
        "find",
        "webfetch",
        "websearch",
        "fetch",
        "task",
        "subagent",
        "todowrite",
        "todoread",
        "notebookread",
        "getdynamictools",
        "awaitshell",
        "switchmode",
        "updatecurrentstep",
        "think",
        "ask",
        "askquestion",
    }
)

#: Prefix a harness uses for an MCP tool. Measured: Cursor sends ``MCP:tavily_search`` — not
#: ``CallDynamicTool`` and not ``mcp__server__tool``, both of which were guesses (B-3/B-4).
#: An MCP tool is a read as far as the filesystem is concerned: it cannot write a project file,
#: so it is allowed during a run. Refusing it denied phase 3 its only search channel.
_MCP_PREFIXES: Final[tuple[str, ...]] = ("mcp:", "mcp__")
#: Ledger line that opts a non-bug-fix run (tooling, refactor) out of the Phase-1
#: write ban. Anchored: a ledger describing the marker must not satisfy it.
_PHASE_GATE_OFF_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t>*_-]{0,8}Phase gate:[ \t]*off\b", re.MULTILINE | re.IGNORECASE
)


@dataclass(frozen=True)
class Verdict:
    """Decision for one tool call.

    Attributes:
        allow: Whether the call may proceed.
        reason: Message for the agent when denied; empty when allowed.
        verdict_of: Stable identifier of the rule that refused (§5.4). Recorded in the
            journal so a refusal can be classified without parsing its wording — a reworded
            `why` used to break that classification silently (TASK-086 F10).
    """

    allow: bool
    reason: str = ""
    verdict_of: str = ""


@dataclass(frozen=True)
class RecordLink:
    """One entry of a hypothesis's ``#### Records`` list.

    Attributes:
        id: Record that produced the verdict, e.g. ``"F3"``.
        verdict: Verdict keyword as written, e.g. ``"REFUTED"``.
        reason: Text after the verdict, as written. Only ``SKIPPED`` requires it — an
            unexplained skip is indistinguishable from a forgotten one (§5.1).
    """

    id: str
    verdict: str
    reason: str = ""


@dataclass(frozen=True)
class Hypothesis:
    """A ``### H<n>`` sub-task (§5.1).

    Attributes:
        id: Hypothesis id, e.g. ``"H2"``.
        assertion: The one-line claim from the heading.
        rank: Probe order, 1-based; ``0`` when absent or unreadable.
        status: Derived from ``records``, never read from the ``Status`` field.
        declared_status: The ``Status`` table field as written; only compared, never trusted.
        proposed: Record that introduced the hypothesis.
        origin: ``description`` (phase 2) or ``logs+search`` (phase 3).
        mechanism: How the cause produces the symptom.
        prediction: What will be observed if the cause is right.
        discriminator: How it differs from a neighbouring hypothesis.
        records: Verdict history, oldest first.
    """

    id: str
    assertion: str
    rank: int
    status: str
    declared_status: str
    proposed: str
    origin: str
    mechanism: str
    prediction: str
    discriminator: str
    records: list[RecordLink]


@dataclass(frozen=True)
class Record:
    """A ``### F<n>`` record: one baseline, research round, or probe (§5.2).

    Attributes:
        id: Record id, e.g. ``"F5"``.
        title: Heading text after the en dash.
        kind: ``route``, ``research``, or ``probe`` — distinguishes the writer.
        scope: ``task`` or a hypothesis id.
        responds_to: Previous record in the chain, or empty.
        supersedes: Record this one replaces, or empty.
        outcome: ``Outcome`` table field, verbatim.
        body: Full block text, for markers that live in prose.
    """

    id: str
    title: str
    kind: str
    scope: str
    responds_to: str
    supersedes: str
    outcome: str
    body: str


class UnknownToolError(RuntimeError):
    """The payload names a tool the gate cannot classify as write or read-only.

    Raised instead of assuming "not a write". A harness that renames a tool, or a
    payload shape the gate has not seen, is a broken wiring: failing closed makes
    it visible on the first call, where silence would let it run for weeks.
    """


class UnreadableCommandError(RuntimeError):
    """A shell tool arrived without a readable command string."""


class UnreadablePayloadError(RuntimeError):
    """stdin held something that is not a JSON object."""


class CounterResetError(RuntimeError):
    """A counter reset that the operator did not authorize.

    ``A`` is the only counter that can be reset, and only by the operator. An
    agent-written reset would make the limit decorative.
    """


class AmbiguousMarkerError(RuntimeError):
    """A marker whose value cannot be determined without guessing."""


class LedgerNotFoundError(RuntimeError):
    """A project contract declares a run, but the ledger it points at cannot be found.

    Raised rather than returning ``None``: "no run is under way" and "I was told there is a run
    and cannot find it" are different answers, and only the first may allow a write. Answering
    ``None`` to both is how the gate turned itself off without a word (spec §0).
    """


class ContractMissingError(RuntimeError):
    """No project contract anywhere above the project directory.

    The plugin cannot do its job without one: the agent would not know the test command, where
    findings go, or what the project forbids (spec §6 phase 0). Raised so the harness reports a
    refusal with a recommendation, rather than leaving a silent gate that protects nothing.
    """


def _allow(note: str = "") -> Verdict:
    """Return an allow verdict, optionally with a note for stderr.

    Args:
        note: Diagnostic text; never shown to the agent as a denial.

    Returns:
        An allowing :class:`Verdict`.
    """
    if note:
        print(f"bugfixer-gate: {note}", file=sys.stderr)
    return Verdict(allow=True)


def _deny(reason: str, *, verdict_of: str) -> Verdict:
    """Return a deny verdict with an actionable reason.

    Args:
        reason: What the agent must do instead.
        verdict_of: Stable identifier of the rule refusing (§5.4). Required rather than
            defaulted: an unclassified refusal is the defect this field exists to remove,
            and a default would let a new `_deny` site reintroduce it silently.

    Returns:
        A denying :class:`Verdict`.
    """
    return Verdict(allow=False, reason=reason, verdict_of=verdict_of)


def _fail_closed(message: str) -> NoReturn:
    """Deny on an internal error that cannot be safely interpreted.

    Args:
        message: What went wrong and how to fix it.
    """
    verdict = _deny(f"{message} (gate failed closed; fix the state or stop the run)", verdict_of="fail-closed")
    _emit(verdict)
    raise SystemExit(EXIT_DENY)


def _emit(verdict: Verdict) -> None:
    """Print the verdict as JSON and exit with the matching code.

    Args:
        verdict: Decision to report.
    """
    payload = {"permission": "allow" if verdict.allow else "deny"}
    if verdict.reason:
        payload["agent_message"] = verdict.reason
        payload["user_message"] = verdict.reason
    print(json.dumps(payload))


# --- payload -----------------------------------------------------------------


def _tool_name(event: dict[str, Any]) -> str:
    """Extract the tool name from a harness payload.

    Args:
        event: Harness event as parsed from stdin.

    Returns:
        Lowercased tool name, or an empty string when absent.
    """
    for key in ("tool_name", "toolName", "tool"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return ""


def _tool_input(event: dict[str, Any]) -> dict[str, Any]:
    """Extract the tool arguments from a harness payload.

    Args:
        event: Harness event.

    Returns:
        Tool arguments, or an empty dict when the payload carries none.

    Raises:
        UnreadablePayloadError: When the arguments are a string that is not JSON —
            silently returning ``{}`` would make the call unclassifiable and, before
            this change, allowed.
    """
    for key in ("tool_input", "toolInput", "args", "arguments", "input"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise UnreadablePayloadError(
                    f"tool arguments in {key!r} are not valid JSON: {exc}"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
            raise UnreadablePayloadError(
                f"tool arguments in {key!r} parsed to a {type(parsed).__name__}, not an object"
            )
    return {}


def _target_path(args: dict[str, Any]) -> str:
    """Extract the file path a write tool is aimed at.

    Args:
        args: Tool arguments.

    Returns:
        Path as written by the agent, or an empty string.
    """
    for key in ("file_path", "filePath", "path", "target_file", "notebook_path"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _shell_command(args: dict[str, Any]) -> str:
    """Extract the command line a shell tool would run.

    Args:
        args: Tool arguments.

    Returns:
        Command string, or an empty string when the payload names none.
    """
    for key in ("command", "cmd", "script", "command_line", "commandLine"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _is_write_attempt(tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Classify a tool call as a write or not.

    Three-way, because "not a write" and "I cannot tell" are different answers:

    * a write tool, or a shell call whose command writes -> ``(True, detail)``;
    * a known read-only tool -> ``(False, "")``;
    * anything else, or a shell call whose command cannot be read -> the gate
      raises, because guessing here is how a broken wiring becomes an allow.

    Args:
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        ``(True, path_or_command)`` when the call mutates the filesystem.

    Raises:
        UnknownToolError: When the call cannot be classified either way.
        UnreadableCommandError: When the tool is a shell but no command is present.
    """
    if tool in _SHELL_TOOLS:
        command = _shell_command(args)
        if not command:
            raise UnreadableCommandError(
                f"shell tool {tool!r} carried no readable command in its payload "
                f"(keys seen: {sorted(args)})"
            )
        return _is_write_command(command), command
    if tool in _WRITE_TOOLS:
        return True, _target_path(args)
    if tool in _SAFE_TOOLS or tool.startswith(_MCP_PREFIXES):
        return False, ""
    raise UnknownToolError(
        f"tool {tool!r} is neither a known write tool, a known shell tool, nor a known "
        "read-only tool"
    )


# --- ledger ------------------------------------------------------------------


def _resolve_ledger(event: dict[str, Any], explicit: str | None) -> Path | None:
    """Find the ledger this run writes to.

    Three sources, in order, and the order is deliberate:

    1. ``--ledger`` from the adapter — an explicit answer beats discovery.
    2. A path in the hook payload (some harnesses pass one).
    3. **The project contract or the working directory.** This is what makes a
       portable install possible: a hook command cannot embed an absolute ledger
       path, because the ledger does not exist when the skill is installed and its
       location differs per project.

    Step 3 reads ``Task/bug file:`` from the contract and takes the newest file
    matching its pattern. Anything else — several candidates that are equally new,
    a pattern that matches nothing — returns ``None``, because guessing which bug is
    being fixed is worse than the gate staying inert.

    Args:
        event: Harness event, which may carry a path and a working directory.
        explicit: ``--ledger`` value from the adapter.

    Returns:
        Path to an existing ledger, or ``None`` when no run can be identified.
    """
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    for key in ("ledger", "ledger_path", "task_file"):
        value = event.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)

    for raw in candidates:
        path = Path(raw).expanduser()
        if path.is_file():
            return path

    return discover_ledger(_project_root(event))


def _project_root(event: dict[str, Any]) -> Path:
    """Working directory the hook is called in, or the process directory.

    Cursor documents ``cwd`` for some events and ``workspace_roots`` for all of
    them, and reports ``cwd`` as an empty string for Shell calls in some versions.
    The empty string is treated as absent rather than as the filesystem root.

    Args:
        event: Harness event.

    Returns:
        Directory to search for the contract and the ledger.
    """
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).expanduser().resolve()
    roots = event.get("workspace_roots")
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str) and root.strip():
                return Path(root).expanduser().resolve()
    return Path.cwd()


#: How many directory levels up to look for the contract.
_CONTRACT_SEARCH_DEPTH: Final[int] = 4

#: The file name, in one place so every message can name it without repeating the literal.
CONTRACT_NAME_HINT: Final[str] = "bug-fixer.project.md"


def discover_ledger(root: Path) -> Path | None:
    """Locate the ledger from the project contract, without an explicit path.

    Two failures, two different pieces of advice, because the fixes differ:

    * no contract anywhere above ``root`` → :class:`ContractMissingError`: the plugin has
      nothing to work from, so it asks for `bugfixer init`;
    * a contract that names no usable pattern, matches nothing, or matches several equally new
      files → :class:`LedgerNotFoundError`: the contract exists, and it is incomplete or
      ambiguous.

    ``None`` is not returned: a plugin whose job is to constrain the agent cannot constrain it
    without being told what to constrain (spec §0, §6 phase 0).

    Args:
        root: Directory to start from (usually the project root).

    Returns:
        The newest file matching the contract's ``Task/bug file:`` pattern.

    Raises:
        ContractMissingError: When there is no contract at or above ``root``.
        LedgerNotFoundError: When a contract exists but yields no single ledger.
    """
    contract = _find_contract(root)
    if contract is None:
        raise ContractMissingError(
            f"no {CONTRACT_NAME_HINT} at or above {root}. Without it the agent would not know "
            "the test command, where findings go, or what this project forbids, so no run can "
            "start. Run `bugfixer init` to write a starting contract, fill in its `<…>` "
            "placeholders, then confirm with `bugfixer doctor`."
        )
    pattern = _contract_ledger_pattern(contract)
    if not pattern:
        raise LedgerNotFoundError(
            f"{contract} declares a run but names no usable ledger. Add a line "
            "`Task/bug file: <path or glob>`, for example `Task/bug file: tasks/TASK-*.md`, "
            "then run `bugfixer doctor` to confirm the gate can find it."
        )

    matches: list[Path] = []
    for base in (root, contract.parent):
        matches.extend(sorted(base.glob(pattern)))
    # The same file can be matched twice (root and the contract's directory coincide);
    # dedupe by resolved path, or the mtime tie-break below would see a false tie.
    files = sorted({match.resolve() for match in matches if match.is_file()})
    if not files:
        raise LedgerNotFoundError(
            f"{contract} points at `{pattern}`, which matches no file under {root}. Either the "
            "task file does not exist yet — create it, because the ledger is where the work is "
            "recorded — or the pattern is wrong. `bugfixer doctor` reports the pattern the gate "
            "actually reads."
        )
    newest = max(files, key=lambda match: match.stat().st_mtime)
    # Two *different* files with the same mtime cannot be ordered; picking one would
    # attach the run to the wrong bug.
    tied = [match for match in files if match.stat().st_mtime == newest.stat().st_mtime]
    if len(tied) > 1:
        names = ", ".join(sorted(match.name for match in tied))
        raise LedgerNotFoundError(
            f"`{pattern}` matches several files with the same timestamp ({names}), so the gate "
            "cannot tell which bug is being fixed. Touch the one you are working on so it is "
            "newest, or name it explicitly with `--ledger <path>`."
        )
    return newest


def _find_contract(root: Path) -> Path | None:
    """Find ``bug-fixer.project.md`` at or above ``root``.

    Args:
        root: Directory to start from.

    Returns:
        Contract path, or ``None``.
    """
    current = root
    for _ in range(_CONTRACT_SEARCH_DEPTH):
        candidate = current / "bug-fixer.project.md"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _contract_ledger_pattern(contract: Path) -> str:
    """Read the ledger pattern from a contract.

    Args:
        contract: Contract file.

    Returns:
        A glob pattern, or empty when the field is missing or unreadable.
    """
    text = contract.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"^[ \t>*-]*(?:Task/bug file|Ledger)[ \t]*:[ \t]*(.+)$", text, re.MULTILINE | re.IGNORECASE
    )
    if match is None:
        return ""
    raw = match.group(1).strip().strip("`").strip()
    # The template documents alternatives as `a | b | c`; the first is the convention
    # to try, and the rest are for projects that keep tasks elsewhere.
    for option in raw.split("|"):
        candidate = option.strip().strip("`").strip()
        if candidate and any(char in candidate for char in "*?["):
            return candidate
    for option in raw.split("|"):
        candidate = option.strip().strip("`").strip()
        if candidate and candidate.endswith(".md"):
            return candidate
    return ""


def read_status(ledger_text: str) -> str:
    """Read the run status from a dedicated status line in the ledger.

    Only a line that *starts* with ``STATUS:`` (optionally after list or quote
    markers) counts. Prose that mentions the marker must not be mistaken for this
    run's status: TASK-078 says "STATUS: BLOCKED" inside its own rules table, and
    reading that as a live status would block a finished task forever.

    ``PARTIAL`` is terminal for the automaton in 3.0: the experience lives in
    ``## Remaining`` and in the refuted hypotheses, so the next attempt is a new
    task that reads this one. Writes are still denied — a partial run is finished.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        ``BLOCKED`` if any status line says so, else ``LIMIT_REACHED``, ``DONE``,
        ``PARTIAL``, else ``RUNNING``. ``UNREADABLE`` when a status line exists but
        carries a word the gate does not know — invariant 15: an unknown status is
        denied, not read as ``RUNNING``.
    """
    known = {"BLOCKED", "LIMIT_REACHED", "DONE", "PARTIAL", "RUNNING"}
    found: set[str] = set()
    for match in re.finditer(
        r"^[ \t>*_-]{0,8}STATUS:[ \t]*([A-Za-z_-]+)", ledger_text, re.MULTILINE
    ):
        word = match.group(1).upper()
        if word not in known:
            return "UNREADABLE"
        found.add(word)
    for status in ("BLOCKED", "LIMIT_REACHED", "DONE", "PARTIAL"):
        if status in found:
            return status
    return "RUNNING"


# --- ledger readers (v3) -----------------------------------------------------


def _block_body(text: str, start: int) -> str:
    """Slice a ``###`` block from its heading to the next level-2/3 heading.

    Args:
        text: Full ledger contents.
        start: Offset of the block heading.

    Returns:
        Block text, heading included.
    """
    end = _BLOCK_END_RE.search(text, start + 1)
    return text[start : end.start() if end else len(text)]


def _fields(block: str) -> dict[str, str]:
    """Read a block's ``| Field | Value |`` table.

    Args:
        block: Block text.

    Returns:
        Lowercased field name to value. First occurrence wins, so a later duplicate
        cannot silently override the field a reader already used.
    """
    fields: dict[str, str] = {}
    for name, value in _TABLE_ROW_RE.findall(block):
        key = name.strip().lower()
        if key in {"field", "value"} or set(key) <= {"-", ":"}:
            continue
        fields.setdefault(key, value.strip())
    return fields


def _records_section(block: str) -> str:
    """Slice the ``#### Records`` list out of a hypothesis block.

    Args:
        block: Hypothesis block text.

    Returns:
        Text after the ``#### Records`` heading, or empty when absent.
    """
    match = re.search(r"^####\s+Records\s*$", block, re.MULTILINE)
    if match is None:
        return ""
    tail = block[match.end() :]
    next_heading = re.search(r"^#{2,4}\s", tail, re.MULTILINE)
    return tail[: next_heading.start()] if next_heading else tail


def _record_links(block: str) -> list[RecordLink]:
    """Parse the ``#### Records`` bullets of a hypothesis block.

    Args:
        block: Hypothesis block text.

    Returns:
        Links in file order, which is the order the verdicts happened in.
    """
    return [
        RecordLink(id=record_id, verdict=verdict.strip(), reason=reason.strip())
        for record_id, verdict, reason in _RECORD_LINK_RE.findall(_records_section(block))
    ]


def derive_status(hypothesis: Hypothesis) -> str:
    """Derive a hypothesis status from its records.

    The ``Status`` table field is a display convenience and is never the source of
    truth: a field can be edited without doing the work, a record cannot.

    ``PARTIALLY`` and ``DEGRADED`` keep the hypothesis **unchecked** (``PROPOSED``):
    a partial hit is deepened by the same hypothesis, not retired, so it stays in
    the queue.

    ``SKIPPED`` leaves the queue although nothing was probed: another hypothesis reached
    ``CONFIRMED`` and the goal is met, so a further probe would be work without a target
    (§5.1, TASK-086 ST-13). It is written as a record, so the fact that the hypothesis was
    never checked stays visible in the file.

    Args:
        hypothesis: Parsed hypothesis.

    Returns:
        ``PROPOSED``, ``CONFIRMED``, ``REFUTED``, or ``SKIPPED``.
    """
    if not hypothesis.records:
        return "PROPOSED"
    verdict = hypothesis.records[-1].verdict.upper()
    if verdict.startswith("CONFIRMED"):
        return "CONFIRMED"
    if verdict.startswith("REFUTED"):
        return "REFUTED"
    if verdict.startswith("SKIPPED"):
        return "SKIPPED"
    return "PROPOSED"


def parse_hypotheses(text: str) -> dict[str, Hypothesis]:
    """Parse every ``### H<n> — …`` block in the ledger.

    One parser for ``H0`` and ``H1…Hn``: a second format would drift from the
    first, and ``H0`` is not special enough to earn one.

    Args:
        text: Full ledger contents.

    Returns:
        Hypothesis id to parsed hypothesis. Duplicate ids keep the first block and
        are reported by the caller, not silently merged.
    """
    hypotheses: dict[str, Hypothesis] = {}
    for match in _HYPOTHESIS_HEADING_RE.finditer(text):
        hyp_id = match.group(1)
        block = _block_body(text, match.start())
        fields = _fields(block)
        raw_rank = fields.get("rank", "")
        rank = int(raw_rank) if raw_rank.isdigit() else 0
        draft = Hypothesis(
            id=hyp_id,
            assertion=match.group(2).strip(),
            rank=rank,
            status="PROPOSED",
            declared_status=fields.get("status", ""),
            proposed=fields.get("proposed", ""),
            origin=fields.get("origin", ""),
            mechanism=fields.get("mechanism", ""),
            prediction=fields.get("prediction", ""),
            discriminator=fields.get("discriminator", ""),
            records=_record_links(block),
        )
        hypotheses.setdefault(
            hyp_id, replace(draft, status=derive_status(draft))
        )
    return hypotheses


def parse_records(text: str) -> list[Record]:
    """Parse every ``### F<n> — …`` block in the ledger.

    Args:
        text: Full ledger contents.

    Returns:
        Records in file order.
    """
    records: list[Record] = []
    for match in _RECORD_HEADING_RE.finditer(text):
        block = _block_body(text, match.start())
        fields = _fields(block)
        records.append(
            Record(
                id=match.group(1),
                title=match.group(2).strip(),
                kind=fields.get("kind", "").lower(),
                scope=fields.get("scope", ""),
                responds_to=fields.get("responds to", ""),
                supersedes=fields.get("supersedes", ""),
                outcome=fields.get("outcome", ""),
                body=block,
            )
        )
    return records


def count_attempts(text: str) -> int:
    """Count probes that reached a code change, honouring an operator reset.

    Args:
        text: Full ledger contents.

    Returns:
        Number of ``Attempt <k> consumed`` lines after the last authorized reset.

    Raises:
        CounterResetError: When any reset line is not the strict operator form.
            A reset the operator did not authorize would make the cap decorative.
    """
    resets = list(_COUNTER_RESET_ANY_RE.finditer(text))
    authorized = list(_COUNTER_RESET_RE.finditer(text))
    if len(resets) != len(authorized):
        raise CounterResetError(
            "a `Counter reset:` line is missing its authorization. Only the operator may "
            "reset the counter, and the line must read "
            "`Counter reset: A -> 0 (by operator: <reason>)`. An agent-written reset is "
            "not a reset."
        )
    start = authorized[-1].end() if authorized else 0
    return len(_ATTEMPT_RE.findall(text[start:]))


def count_rechecks(text: str, hypothesis: str) -> int:
    """Count re-runs of one hypothesis's target tests.

    Args:
        text: Full ledger contents.
        hypothesis: Hypothesis id, e.g. ``"H1"``.

    Returns:
        Highest ``Recheck H<n> #<k>`` number for that hypothesis, or ``0``.
    """
    numbers = [
        int(number)
        for hyp, number in _RECHECK_RE.findall(text)
        if hyp.upper() == hypothesis.upper()
    ]
    return max(numbers) if numbers else 0


def active_hypothesis(text: str) -> str:
    """Read the ``Working on: H<n>`` pointer.

    Args:
        text: Full ledger contents.

    Returns:
        Hypothesis id, or empty when no marker is present.

        Raises:
        AmbiguousMarkerError: When more than one marker line exists. The marker is
            moved by rewriting it, so two lines mean the rewrite went wrong — and
            guessing which one is live would attach an edit to the wrong hypothesis.    """
    markers = _WORKING_ON_RE.findall(text)
    if len(markers) > 1:
        raise AmbiguousMarkerError(
            "the ledger has more than one `Working on:` line ("
            + ", ".join(markers)
            + "). Rewrite the single marker instead of appending a second one, so an "
            "edit cannot be attributed to the wrong hypothesis."
        )
    return markers[0] if markers else ""


def count_hypotheses(text: str) -> int:
    """Count ``### H<n>`` blocks.

    Args:
        text: Full ledger contents.

    Returns:
        Number of hypothesis headings, duplicates included.
    """
    return len(_HYPOTHESIS_HEADING_RE.findall(text))


def unchecked_hypotheses(text: str) -> list[Hypothesis]:
    """List hypotheses that still need a probe, ordered by ``Rank``.

    Args:
        text: Full ledger contents.

    Returns:
        Hypotheses whose derived status is ``PROPOSED`` (including a partial hit that
        must be deepened), sorted by rank then id.
    """
    parsed = parse_hypotheses(text).values()
    return sorted(
        (hypothesis for hypothesis in parsed if hypothesis.status in {"PROPOSED"}),
        key=lambda hypothesis: (hypothesis.rank, hypothesis.id),
    )


def table_form_attempt(text: str) -> bool:
    """Whether the ledger writes an attempt or recheck as a table row.

    Args:
        text: Full ledger contents.

    Returns:
        ``True`` when the rejected table form is present.
    """
    return bool(_TABLE_ATTEMPT_RE.search(text) or _TABLE_RECHECK_RE.search(text))


def checked_hypotheses(text: str) -> list[Hypothesis]:
    """List hypotheses that already have a verdict.

    Args:
        text: Full ledger contents.

    Returns:
        Hypotheses whose derived status is not ``PROPOSED``, sorted by rank.
    """
    return sorted(
        (hypothesis for hypothesis in parse_hypotheses(text).values() if hypothesis.status != "PROPOSED"),
        key=lambda hypothesis: (hypothesis.rank, hypothesis.id),
    )


def counter_problems(text: str) -> list[str]:
    """Validate attempt, recheck and hypothesis numbering.

    Numbering is a real signal: ``Attempt`` ``{1..n}`` with a gap means a line was
    deleted, and an attempt count that cannot be trusted makes every cap
    meaningless. ``Recheck`` is per hypothesis and does not touch ``A``: fixing a
    broken test is repairing the instrument, not new work.

    Args:
        text: Full ledger contents.

    Returns:
        Refusals, in the order the ledger should fix them. Empty when numbering is
        sound.
    """
    problems: list[str] = []

    try:
        count_attempts(text)  # raises on an unauthorized reset
    except CounterResetError as exc:
        problems.append(str(exc))
        return problems

    reset_at = [match.end() for match in _COUNTER_RESET_RE.finditer(text)]
    start = reset_at[-1] if reset_at else 0
    attempts = [int(number) for number in _ATTEMPT_RE.findall(text[start:])]
    expected = 1
    for number in attempts:
        if number != expected:
            problems.append(
                f"Attempt numbering has a gap at {expected}: found `Attempt {number} consumed`. "
                "Attempts are numbered from 1 with no gaps, so a deleted line is visible."
            )
            break
        expected += 1
    total = len(attempts)
    if total > MAX_ATTEMPTS:
        problems.append(
            f"{total} attempts recorded, cap is {MAX_ATTEMPTS}. Write `STATUS: LIMIT_REACHED` "
            "with `## Report` instead of another change."
        )

    for hypothesis, numbers in _rechecks_by_hypothesis(text).items():
        expected_number = 1
        for number in numbers:
            if number != expected_number:
                problems.append(
                    f"`Recheck {hypothesis}` numbering has a gap at #{expected_number}: "
                    f"found #{number}."
                )
                break
            expected_number += 1
        highest = max(numbers)
        if highest > MAX_RECHECKS:
            problems.append(
                f"`Recheck {hypothesis} #{highest}` exceeds the limit of {MAX_RECHECKS}. "
                "Reformulate the hypothesis or admit it is not testable — repeating the same "
                "run a sixth time is not evidence."
            )

    hypotheses = count_hypotheses(text)
    if hypotheses > MAX_HYPOTHESES:
        problems.append(
            f"{hypotheses} hypotheses recorded, limit is {MAX_HYPOTHESES}. Keep the ten most "
            "probable: thirty hypotheses with none probed is not research."
        )

    for hypothesis_id in _rechecked_hypotheses(text):
        if recheck_without_probe(text, hypothesis_id):
            problems.append(
                f"`Recheck {hypothesis_id}` without a probe: there is no verdict to repeat. "
                "A recheck repairs the test after a run that did not settle the question."
            )
            break

    return problems


def _rechecks_by_hypothesis(text: str) -> dict[str, list[int]]:
    """Group recheck numbers by hypothesis, in file order.

    Args:
        text: Full ledger contents.

    Returns:
        Hypothesis id to its recheck numbers.
    """
    grouped: dict[str, list[int]] = {}
    for hypothesis, number in _RECHECK_RE.findall(text):
        grouped.setdefault(hypothesis, []).append(int(number))
    return grouped


def _rechecked_hypotheses(text: str) -> list[str]:
    """Hypotheses named by a ``Recheck`` line, including ones with no block.

    A recheck for a hypothesis that does not exist is exactly the case worth
    reporting; iterating the parsed blocks would silently skip it.

    Args:
        text: Full ledger contents.

    Returns:
        Hypothesis ids in first-seen order.
    """
    return list(_rechecks_by_hypothesis(text))


def recheck_without_probe(text: str, hypothesis: str) -> bool:
    """Whether a hypothesis has rechecks but no probe at all.

    Args:
        text: Full ledger contents.
        hypothesis: Hypothesis id.

    Returns:
        ``True`` when rechecks exist for a hypothesis that was never probed.
    """
    rechecked = any(
        hyp.upper() == hypothesis.upper() for hyp, _ in _RECHECK_RE.findall(text)
    )
    if not rechecked:
        return False
    probed = any(
        record.scope.upper() == hypothesis.upper()
        for record in parse_records(text)
        if record.kind in {"probe", "research"}
    )
    return not probed


def task_fingerprint(text: str) -> str:
    """Fingerprint of the ``## Task`` block.

    The task may legitimately be refined; ``## Clarifications`` exists for that. But
    if the task block itself now says something else, the report should say so:
    fixing a different bug than the one filed is not a detail. This is a
    **warning**, not a refusal — the operator decides whether the task was
    clarified or replaced.

    Args:
        text: Full ledger contents.

    Returns:
        Short fingerprint, or empty when there is no ``## Task`` block.
    """
    match = _TASK_BLOCK_RE.search(text)
    if match is None:
        return ""
    body = " ".join(match.group(1).split())
    if not body:
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:_TASK_FINGERPRINT_CHARS]


def task_drift(ledger: Path, text: str) -> str:
    """Compare the task fingerprint with the one recorded at run start.

    Args:
        ledger: Ledger file.
        text: Full ledger contents.

    Returns:
        Warning text when the task changed, else empty. The first sight of a task is
        recorded as a ``start`` record in the journal, so the baseline is the first
        version ever read and there is no second state file to keep in step.
    """
    current = task_fingerprint(text)
    if not current:
        return ""
    recorded = _recorded_fingerprint(ledger)
    if recorded is None:
        journal.append_event(
            ledger,
            event="start",
            why="first sight of the task; its text is the baseline for drift",
            task_fingerprint=current,
        )
        return ""
    if recorded != current:
        return (
            "the `## Task` block no longer matches the text this run started with. "
            "Refinements belong in `## Clarifications`; if the task itself changed, say so "
            "in the report — a correct fix for a different bug is not a fix."
        )
    return ""


def _recorded_fingerprint(ledger: Path) -> str | None:
    """The task fingerprint the run started with, if one was recorded.

    Args:
        ledger: Ledger file.

    Returns:
        Fingerprint string, or ``None`` when the journal holds no ``start`` record.
    """
    try:
        events = journal.read_events(ledger)
    except journal.JournalError as exc:
        _fail_closed(str(exc))
    for record in events:
        if record.get("event") == "start" and record.get("task_fingerprint"):
            return str(record["task_fingerprint"])
    return None


def scan_records(text: str) -> list[str]:
    """Scan every ``### F<n>`` block for secrets.

    Records are where logs and command output end up, so they are where a leaked
    value is most likely to sit — and the place the gate can actually see.

    Args:
        text: Full ledger contents.

    Returns:
        Refusal messages, one per secret found. Empty when clean.
    """
    problems: list[str] = []
    for record in parse_records(text):
        for finding in secrets.scan(record.body):
            problems.append(
                f"secret in `{record.id}` at {secrets.describe(finding)}. Remove it: report "
                "only whether the secret is configured (`api_key: configured`). Its value, "
                "length, hash and prefix all help to recover it and are refused too (§10)."
            )
    return problems


#: How deep the sub-task scan descends. A sub-task lives at `tasks/TASK-<n>/ST-<k>.md`; one more
#: level covers a sub-task that grew its own folder. Bounded rather than unbounded: a runaway
#: tree under `tasks/` should not turn one hook call into a crawl of the project.
_SUBTASK_SCAN_DEPTH: Final[int] = 2

#: A sub-task row of the ledger's status table (§5.3a). The marker is the first character of the
#: status cell; everything after it is written for a human and is not parsed.
_SUBTASK_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"^\|\s*\*{0,2}(ST-[\w.]+)\*{0,2}\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", re.MULTILINE
)

#: Markers that mean "the gate may treat this sub-task as closed". Everything else is open.
_CLOSED_SUBTASK_MARKERS: Final[frozenset[str]] = frozenset({"✅", "⏭", "❌"})

#: Markers whose meaning depends on a written reason. A skip without one is an omission.
_SUBTASK_NEEDS_REASON: Final[frozenset[str]] = frozenset({"⏭"})


@dataclass(frozen=True)
class SubtaskStatus:
    """One row of the ledger's sub-task status table (§5.3a).

    Attributes:
        id: ``ST-<k>`` as written.
        title: The sub-task's one-line description.
        marker: First token of the status cell — the only part a machine reads.
        text: The status cell after the marker, written for a human.
    """

    id: str
    title: str
    marker: str
    text: str

    @property
    def closed(self) -> bool:
        """Whether the marker says the sub-task needs nothing further."""
        return self.marker in _CLOSED_SUBTASK_MARKERS


def parse_subtasks(text: str) -> list[SubtaskStatus]:
    """Read the sub-task status table out of the ledger (§5.3a).

    The table is the only place a machine can learn whether a sub-task is finished: a status
    written in a sub-file is invisible, because sub-files are opened for secrets and ``### F<n>``
    records only. Measured on ``TASK-086``: its hypotheses and sub-tasks exist only as rows, and
    the gate saw none of them.

    Args:
        text: Full ledger contents.

    Returns:
        Rows in file order; empty when the ledger has no such table.
    """
    found: list[SubtaskStatus] = []
    for match in _SUBTASK_ROW_RE.finditer(text):
        title = match.group(2).strip().strip("*")
        cell = match.group(4).strip()
        if not cell or set(cell) <= {"-", ":", " "}:
            continue
        marker, _, rest = cell.partition(" ")
        found.append(
            SubtaskStatus(
                id=match.group(1),
                title=title,
                marker=marker,
                text=rest.strip().strip("*").strip(),
            )
        )
    return found


def subtask_status_problems(text: str) -> list[str]:
    """Refuse a sub-task table whose statuses cannot be trusted.

    Two ways a table lies, both measured shapes rather than imagined ones:

    * a marker outside the documented set — the row then means nothing to a reader or a machine;
    * a row with neither a marker nor text — the sub-task is in the table but says nothing.

    A skip without a reason is checked separately, because the reason is what distinguishes a
    decision from an omission.

    Args:
        text: Full ledger contents.

    Returns:
        Refusals, in row order. Empty when every row is readable.
    """
    problems: list[str] = []
    valid = _CLOSED_SUBTASK_MARKERS | {"⚠️", "⬜"}
    for row in parse_subtasks(text):
        if row.marker not in valid:
            problems.append(
                f"`{row.id}` has status {row.marker!r}, which is not one of "
                f"{sorted(valid)}. The marker is the first character of the status cell; "
                "write `✅` (done), `⚠️` (partial), `⬜` (open), `⏭` (skipped) or `❌` (rejected), "
                "then the explanation."
            )
        elif row.marker in _SUBTASK_NEEDS_REASON and not row.text:
            problems.append(
                f"`{row.id}` is marked `⏭` without a reason. Say which sub-task or hypothesis "
                "made it unnecessary; a bare skip reads as an omission (§5.3a)."
            )
    return problems


def subtask_directory(ledger: Path) -> Path:
    """The directory a ledger's sub-tasks live in (spec §5.3a).

    The name is the task number only — `tasks/TASK-086/` for `tasks/TASK-086-…md`, not the whole
    file stem. Using the stem looked right and was wrong: the real directory was `TASK-086`, so
    the scan found nothing and reported a clean task while three sub-task files sat unread.

    Args:
        ledger: Ledger file.

    Returns:
        Path of the sub-task directory; it may not exist.
    """
    match = re.match(r"(TASK-\d+)", ledger.stem)
    name = match.group(1) if match else ledger.stem
    return ledger.parent / name


def subtask_files(ledger: Path) -> list[Path]:
    """Markdown files in the ledger's own sub-task directory, if it exists.

    Spec §5.3a puts sub-tasks at ``tasks/TASK-<n>/ST-<k>.md`` — a directory named after the task
    number, which the contract's ``tasks/TASK-*.md`` glob does not match (that is why the
    directory is used at all). Records may live there, so the secret scan must follow them.

    Args:
        ledger: Ledger file.

    Returns:
        Markdown files under the sub-task directory, sorted, empty when it is absent.
    """
    folder = subtask_directory(ledger)
    if not folder.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(folder.rglob("*.md")):
        relative = path.relative_to(folder)
        if len(relative.parts) > _SUBTASK_SCAN_DEPTH:
            continue
        if path.is_file():
            found.append(path)
    return found


def secret_problems(ledger: Path, text: str) -> list[str]:
    """Every secret the run would be refused for, across the ledger and its sub-task files.

    Invariant 26 covers a record wherever it is written. Splitting a task into sub-files moved
    records out of the ledger's text, and a scan of that text alone silently stopped covering
    them: measured, the same poisoned record gave one finding inside the ledger and zero after
    being moved (TASK-086, probe `check_records_scan_scope.py`).

    Args:
        ledger: Ledger file.
        text: Ledger contents, already read by the caller.

    Returns:
        Refusal messages in file order: the ledger first, then each sub-task file.
    """
    problems = scan_records(text)
    for path in subtask_files(ledger):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # An unreadable sub-file cannot be shown to be clean; say so rather than skip it.
            problems.append(
                f"cannot read the sub-task file {path.name} to check it for secrets ({path}). "
                "Fix its permissions or remove it from the task folder."
            )
            continue
        for finding in secrets.scan(body):
            problems.append(
                f"secret in {path.name} at {secrets.describe(finding)}. Remove it: report only "
                "whether the secret is configured (`api_key: configured`). Its value, length, "
                "hash and prefix all help to recover it and are refused too (§10)."
            )
    return problems


def run_state(ledger: Path) -> dict[str, Any]:
    """Derive the run state from the journal.

    There is no sidecar any more (invariant 34): the journal's last matching record is the
    state, so the gate, the state CLI and the drift check cannot disagree.

    Args:
        ledger: Ledger file.

    Returns:
        Mapping with ``phase``, ``status``, ``hypothesis`` and ``transitions``.

    Raises:
        SystemExit: When the journal is unreadable — the gate must not guess a phase.
    """
    try:
        return journal.state_of(ledger)
    except journal.JournalError as exc:
        _fail_closed(str(exc))
        raise  # unreachable; _fail_closed exits


def terminal_status_problems(text: str, status: str) -> list[str]:
    """Validate the fields a terminal status must carry with it.

    The status is written *together with* its evidence — a decision nobody can audit is
    not a decision (invariants 19, 28, 29). Each refusal names the missing field, because
    "do not edit code" tells the agent the run is over but not that it forgot something.

    Args:
        text: Full ledger contents.
        status: Status read from the ledger.

    Returns:
        Refusals, in the order the agent should fix them. Empty when the status is complete.
    """
    problems: list[str] = []

    if _REPORT_RE.search(text) is None:
        problems.append(
            f"`STATUS: {status}` without `## Report`. The status and the report are written "
            "by the same step: Итог, корневая причина, что изменено, таблица результатов, "
            "остаток, что нужно от оператора, уроки."
        )

    if status == "PARTIAL":
        heading = _REMAINING_RE.search(text)
        if heading is None:
            problems.append(
                "`STATUS: PARTIAL` without `## Remaining`. A partial run hands over a list: "
                "what is left, why it is not done, how it is verified, and who closes it."
            )
        else:
            tail = text[heading.end() :]
            next_heading = re.search(r"^##[ \t]", tail, re.MULTILINE)
            section = tail[: next_heading.start()] if next_heading else tail
            # A table's header rule carries no content: count only real items, so a lone
            # `|---|---|` does not pass for a handover.
            items = [
                line
                for line in section.splitlines()
                if _REMAINING_ITEM_RE.match(line) and not _REMAINING_TABLE_RULE_RE.match(line)
            ]
            if not items:
                problems.append(
                    "`## Remaining` exists but lists nothing. An empty remainder means the work "
                    "is DONE; add at least one item — a bullet, a numbered entry, or a table row "
                    "stating what is left, why, how it is verified and who closes it."
                )

    if status == "BLOCKED":
        reason = _BLOCKED_REASON_RE.search(text)
        if reason is None:
            problems.append(
                "`STATUS: BLOCKED` without `Причина:`. Name one of: "
                + ", ".join(BLOCKED_REASONS)
                + "."
            )
        elif reason.group(1).strip().strip("`") not in BLOCKED_REASONS:
            problems.append(
                f"`Причина: {reason.group(1).strip()}` is not one of the listed reasons: "
                + ", ".join(BLOCKED_REASONS)
                + "."
            )
        if _LOGS_RE.search(text) is None:
            problems.append(
                "`STATUS: BLOCKED` without `Логи:`. Give the raw-data directory or the key "
                "lines, otherwise the operator has nothing to act on."
            )

    return problems


def legacy_ledger(text: str) -> bool:
    """Whether this ledger was started under bug-fixer 2.x.

    Three signals together, and all three are needed:

    * a ``## Iteration N`` heading — 2.x wrote it as the pass marker;
    * at least one ``### F<n>`` block — so a fresh 3.0 ledger, which has the
      heading but no records yet, is not mistaken for a stale one;
    * **no** ``| Kind |`` field and no ``### H<n>`` — 3.0's records always carry a
      kind, and hypotheses are its whole structure.

    The spec's own rule (``## Iteration N`` and no ``### H<n>``) is not enough on
    its own: phase 1 of a legitimate 3.0 run writes exactly that heading before any
    hypothesis exists, so the naive version refuses every new run at its first step.

    Treating an old ledger as a fresh phase 1 would revive finished work under rules
    it never knew, and each refusal would look like a bug in the gate.

    Args:
        text: Full ledger contents.

    Returns:
        ``True`` when the ledger carries the 2.x signature.
    """
    if _LEGACY_ITERATION_RE.search(text) is None:
        return False
    if count_hypotheses(text) > 0:
        return False
    if not _LEGACY_FINDING_RE.search(text):
        return False
    return re.search(r"^\|\s*Kind\s*\|", text, re.MULTILINE) is None


def read_phase(ledger: Path) -> int:
    """Read the current phase from the journal's last ``advance`` record.

    Args:
        ledger: Ledger file.

    Returns:
        Phase number (1 to 5); ``PHASE 0`` is outside the automaton by design.
        A missing journal means the run has not started: phase 1, not a refusal
        (invariant 36).

    Raises:
        SystemExit: When the journal is unreadable or records an invalid phase — the
            gate must not guess.
    """
    return int(run_state(ledger)["phase"])


def _test_paths(ledger: Path) -> list[str]:
    """Read the project's test path prefixes from its contract.

    One place, one rule: ``bug-fixer.project.md`` in the project root, which is what the
    skill's Step 0 creates. The plugin used to also look for a filled ``repo-contract.md``
    under ``skills/<id>/references`` — the layout of the repository the plugin was written
    in. In any other project that path means nothing, and in a clone of the plugin there is
    no ``skills/`` above it at all.

    Args:
        ledger: Ledger file, used to walk up toward the project root.

    Returns:
        Path prefixes considered tests. Empty when no contract states them.
    """
    for base in [ledger.parent, *ledger.parents]:
        contract = base / "bug-fixer.project.md"
        if not contract.is_file():
            continue
        text = contract.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"^Test paths(?:\s*\([^)]*\))?:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE
        )
        if match:
            return [item.strip().strip("`") for item in match.group(1).split(",") if item.strip()]
    return []


# --- decision ----------------------------------------------------------------


def _is_path_finding(finding: secrets.Finding, line: str) -> bool:
    """Whether a `base64` finding is really a filesystem path.

    The `base64` rule is ``[A-Za-z0-9+/]{40,}={0,2}``. `/` is in its alphabet, so every path
    of 41 characters or more matches it — measured, and it masked ordinary targets until
    TASK-086 F25.

    A path is told from a token by two properties a base64 blob does not have in combination:
    it contains a `/`, and any `=` it carries is at the very end (base64 padding sits at the
    end too, but a path never has `=` in the middle of a segment).

    Only the `base64` rule is subject to this: a token prefix (`ghp_`, `sk-`, `AKIA`) or an
    assignment is a credential wherever it appears.

    The run is taken by offset from ``line``, not re-found inside ``excerpt``: the excerpt is
    the masked line, so a line holding two matches would answer for the wrong one.

    Args:
        finding: One scanner finding of ``line``.
        line: The line the finding came from.

    Returns:
        ``True`` when the finding is a path and may be recorded as-is.
    """
    if finding.rule != "base64":
        return False
    run = line[finding.start : finding.end]
    if "/" not in run:
        return False
    # A path has no `=` except possibly inside a filename; base64 padding is only at the end.
    return "=" not in run.rstrip("=")




def _journal_target(tool: str, args: dict[str, Any]) -> str:
    """The goal of the call, as the journal should record it (§5.4).

    The path for a write tool, the command line for a shell tool — the same value
    ``_is_write_attempt`` already returns, re-derived here because a refusal can be decided
    before that classification runs (an unknown tool, for instance).

    The command line is recorded **whole** (TASK-086 ST-10). It used to be cut at 400
    characters with a ``truncated`` flag, and a cut field answers "what was attempted" as
    poorly as a missing one: the start of a heredoc is visible, the point of it is not. The
    one thing that is not part of the call is a heredoc *body* — data, not invocation — so the
    body becomes ``[body: N lines, M bytes]`` and every argument stays.

    A command line is the first place a credential reaches the journal, so the value passes
    the §10 scanner: on a finding the **matched span** becomes ``<redacted: rule>`` and the rest
    of the call is kept (TASK-086 ST-12). Before that, the whole field was replaced by the rule
    name, which protected the value and destroyed the answer: a four-argument command carrying a
    token was recorded as ``<redacted: assignment>`` alone. §10 forbids the value, not the
    command around it.

    Args:
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        The target as it should be journalled; empty when the payload names none.
    """
    if tool in _SHELL_TOOLS:
        raw = _shell_command(args)
    else:
        raw = _target_path(args)
    if not raw:
        return ""

    # Bodies are summarised first, so the scan sees what will actually be written: a credential
    # that lives only in a heredoc body never reaches the journal, because the body does not.
    recorded = _command_without_heredoc_bodies(raw) if tool in _SHELL_TOOLS else raw

    # A path is not a credential, and the `base64` rule cannot tell them apart: its pattern is
    # `[A-Za-z0-9+/]{40,}` and `/` is in that alphabet, so any path of 41+ characters matches
    # (measured: TASK-086 F25). Masking an ordinary path leaves the field saying nothing, which is
    # the opposite of its purpose.
    #
    # The exemption is therefore per MATCH, not per rule and not per target shape: a base64-looking
    # run is kept only when it is a path — it contains a `/` and every `=` it carries is at the
    # very end (base64 padding). A shell command keeps its exemption for paths *inside* it, while
    # a token pasted into the same command is still masked.
    return secrets.mask(recorded, keep=_is_path_finding)


def decide(
    *,
    ledger: Path | None,
    tool: str,
    args: dict[str, Any],
    dev_plugin: bool = False,
) -> Verdict:
    """Decide whether a tool call is allowed, and record a refusal.

    A refusal is part of the run's history (§5.4 lists ``deny`` among the events), so the
    wrapper writes it down before returning. The record names the tool and carries the
    reason, which is exactly what the journal is for: answering "why was I blocked here"
    later without replaying the run.

    Args:
        ledger: Active ledger, or ``None`` when no bug-fixer run is under way.
        tool: Lowercased tool name.
        args: Tool arguments.
        dev_plugin: Allow edits to the plugin's own skill tree. Set only from the explicit
            `--dev-plugin` flag: a run that can rewrite its guard has no guard (invariant 47).

    Returns:
        The verdict for this call.
    """
    verdict = _decide(ledger=ledger, tool=tool, args=args, dev_plugin=dev_plugin)
    if ledger is not None and not verdict.allow:
        target = _journal_target(tool, args)
        fields: dict[str, Any] = {
            "tool": tool,
            # `phase` is read here rather than threaded through every `_deny` site: it is a
            # property of the run, not of the rule, and the ledger is the only input the
            # journal needs.
            "phase": _phase_or_none(ledger),
            "verdict_of": verdict.verdict_of,
            "ledger": str(ledger.resolve()),
        }
        # A tool the gate cannot classify names no target. Omitting the field says "not
        # recorded"; an empty string would read as "it tried nothing", a different fact.
        if target:
            fields["target"] = target
        try:
            journal.append_event(ledger, event="deny", why=verdict.reason, **fields)
        except OSError:
            # The journal is an observation, not a precondition (invariant 36): a full
            # disk must not turn a documented refusal into a crash the agent cannot read.
            pass
    return verdict


def _phase_or_none(ledger: Path) -> int | None:
    """The run's phase, or ``None`` when the journal cannot be read.

    A refusal is journalled before the phase is necessarily known — a missing ledger, a
    corrupt journal — and inventing "phase 1" would be the silent guess §0 forbids.

    Args:
        ledger: Ledger being enforced.

    Returns:
        Phase 1-5, or ``None``.
    """
    try:
        return int(journal.state_of(ledger)["phase"])
    except (journal.JournalError, OSError):
        return None


def _decide(
    *,
    ledger: Path | None,
    tool: str,
    args: dict[str, Any],
    dev_plugin: bool = False,
) -> Verdict:
    """Decide whether a tool call is allowed.

    Args:
        ledger: Active ledger, or ``None`` when no bug-fixer run is under way.
        tool: Lowercased tool name.
        args: Tool arguments.
        dev_plugin: Allow edits to the plugin's own skill tree (invariant 47).

    Returns:
        The verdict for this call.
    """
    if ledger is None:
        return _deny(
            "no project contract found, so there is nothing to enforce: the agent would not "
            "know which command runs the tests, where findings go, or what this project "
            "forbids. Run `bugfixer init` to write a starting contract, fill in its `<…>` "
            "placeholders, and confirm with `bugfixer doctor` (spec §6 phase 0). If this "
            "project does not use bug-fixer at all, remove the hook instead of leaving it "
            "here — a gate that cannot be configured protects nothing.",
            verdict_of="no-ledger",
        )

    text = ledger.read_text(encoding="utf-8", errors="replace")
    if legacy_ledger(text):
        return _deny(
            "this ledger was started under bug-fixer 2.1 (it has `## Iteration N` and no "
            "`### H<n>` hypotheses). Finish it under 2.1, or start a new ledger for this bug: "
            "its history has no hypotheses, so the 3.0 guards would refuse every transition "
            "and the refusals would look like a broken gate.",
            verdict_of="legacy-ledger",
        )

    try:
        is_write, detail = _is_write_attempt(tool, args)
    except UnknownToolError as exc:
        # An active run plus a call the gate cannot classify is a wiring defect,
        # not a read. Deny so the operator sees it on the first call.
        return _deny(
            f"{exc}. The gate cannot prove this call is safe, so it is denied. "
            "Either the harness renamed a tool (fix the adapter) or the payload "
            "shape changed (fix the gate).",
            verdict_of="unknown-tool",
        )
    except UnreadableCommandError as exc:
        return _deny(
            f"{exc}. A shell call whose command cannot be read cannot be shown to be "
            "test-only, so it is denied during an active run.",
            verdict_of="unreadable-command",
        )

    if not is_write:
        # Invariant 51: an inline script is refused on its **form**, not on what it does. A
        # read-only `python3 -c` reaches this line because the gate classified it as a read —
        # and classifying it correctly would need a Python parser, which §5.5 explains is
        # deliberately out of scope. The refusal therefore says "write a file", not "you wrote".
        if tool in _SHELL_TOOLS and _is_inline_script(detail):
            return _deny(_inline_script_refusal(), verdict_of="inline-script")
        return Verdict(allow=True)

    # Invariant 33: the journal is history, and history is written by the runtime. This
    # check comes before every phase rule so the refusal names the real objection; told
    # "phase 1 forbids this", the agent would wait for phase 4 and try again.
    if _is_journal_write(detail, ledger, tool):
        return _deny(
            f"the run journal {journal.journal_path(ledger).name} is written by the gate, not "
            "by the agent (spec §5.4, invariant 33). Every state change goes through the state "
            f"CLI, which appends one record: `{_cli_path()} state <ledger> advance <phase> "
            "--evidence <why>`. Findings belong in the task file, not in the journal.",
            verdict_of="journal-write",
        )

    status = read_status(text)

    if status == "UNREADABLE":
        return _deny(
            "the ledger's `STATUS:` line is not one of RUNNING, DONE, PARTIAL, "
            "LIMIT_REACHED, BLOCKED. An unreadable status is not read as RUNNING.",
            verdict_of="unreadable-status",
        )

    # Invariants 19/28/29: a terminal status is written together with its evidence. The
    # field check runs *before* the status block so the refusal can name what is missing —
    # an agent told only "the run is over" cannot tell a forgotten field from a broken gate.
    if status in {"DONE", "PARTIAL", "LIMIT_REACHED", "BLOCKED"}:
        # A run cannot be declared finished while a secret still sits in its own files. Ordinary
        # work stays open (operator's ruling 2026-11-28 15:02: access is not refused); the finish
        # is, because the status is the statement "this is done" (invariant 26, §10).
        lingering = secret_problems(ledger, text)
        if lingering:
            return _deny(
                "the task still holds a secret, so the run cannot be declared finished: "
                + lingering[0]
                + " Remove it first — the status would record a finished run beside an "
                "unresolved leak (§10, invariant 26).",
                verdict_of="secret-in-record",
            )
        field_problems = terminal_status_problems(text, status)
        if field_problems:
            return _deny(
                field_problems[0] + " The run is over either way, so this is written down "
                "rather than fixed by editing code.",
                verdict_of="terminal-field",
            )

    if status in {"DONE", "PARTIAL"}:
        return _deny(
            f"This bug is already marked STATUS: {status}. A partial run is finished, not "
            "paused: the remainder is listed for the next task, so editing code here would "
            "finish a run nobody is tracking. Start a new ledger.",
            verdict_of="terminal-status",
        )
    if status == "LIMIT_REACHED":
        return _deny(
            f"This bug is marked STATUS: LIMIT_REACHED — the attempt cap ({MAX_ATTEMPTS}) is "
            "spent. Do not edit code: report, list every refuted hypothesis, and ask the user for "
            "artefacts.",
            verdict_of="terminal-status",
        )
    if status == "BLOCKED":
        return _deny(
            "This bug is marked STATUS: BLOCKED. Do not edit code. Ask the user for the "
            "artefacts you listed and start a new run when they arrive.",
            verdict_of="terminal-status",
        )

    # Invariant 3: at ``A = 10`` the only correct next action is ``LIMIT_REACHED``. This is
    # checked before the phase-4 marker rules, because a refused cap is not an instruction
    # about markers — telling the agent to add `Working on:` when the run is over is the
    # wrong next step.
    try:
        attempts = count_attempts(text)
    except CounterResetError as exc:
        return _deny(str(exc), verdict_of="counter-reset")
    if attempts >= MAX_ATTEMPTS:
        return _deny(
            f"Attempt cap reached ({attempts} of {MAX_ATTEMPTS}). Write "
            "`STATUS: LIMIT_REACHED` with `## Report` instead of another change.",
            verdict_of="attempt-cap",
        )

    phase = read_phase(ledger)
    # Invariant 43 before every phase rule: the boundary is not a phase question. Told "phase 1
    # forbids this", an agent would wait for phase 4 and try the same out-of-project write again.
    outside = _outside_project_targets(detail, ledger, tool)
    if outside:
        return _deny(
            "this writes outside the project ("
            + ", ".join(outside)
            + "). The plugin works inside the open project and its subdirectories only: no "
            "writes to /tmp or any other system directory, in any phase. Keep temporary files "
            "inside the project — the `Raw dumps:` directory of the contract, or the task file "
            "itself. If a system file really has to change, print the command and let the user "
            "run it (spec §15, invariant 43).",
            verdict_of="outside-project",
        )
    # Invariant 51 for the writing form: a heredoc into an interpreter is classified as a write,
    # so it reaches here rather than the "not a write" branch above. Placed after the journal and
    # project-boundary rules (each names a more specific objection) and before the changelog and
    # phase rules: the ban is about the form, so "wait for phase 4" would be the wrong advice —
    # the same command is refused in phase 4 too.
    if tool in _SHELL_TOOLS and _is_inline_script(detail):
        return _deny(_inline_script_refusal(), verdict_of="inline-script")
    # Invariant 49: `CHANGELOG.md` is a claim that a change is finished, so it is judged by
    # evidence rather than by phase. Placed before the phase rules because "wait for phase 4"
    # would be the wrong advice — the entry is written *after* a probe proved itself, in any phase.
    if _is_changelog_write(detail, tool):
        problem = _changelog_problem(args, ledger)
        if problem:
            return _deny(problem, verdict_of="changelog-evidence")
        # The claim is backed by evidence, so the phase ban does not apply to this file.
        return Verdict(allow=True)

    if phase != 4 and phase_gate_enabled(text) and _is_code_write(
        detail, ledger, tool, dev_plugin=dev_plugin
    ):
        return _deny(
            f"Phase {phase} forbids production-code changes: only Phase 4 (probe) may edit "
            "code, and only as a probe of a named hypothesis. Phases 1-3 collect evidence; "
            f"Phase 5 judges it. Run `{_cli_path()} state <ledger> advance 4 --edge "
            "FIRST_GUESS --evidence \"H0 created\"` (or HYPOTHESIS_READY) once the "
            "preconditions are written.",
            verdict_of="phase-ban",
        )

    if phase == 4 and phase_gate_enabled(text) and _is_code_write(
        detail, ledger, tool, dev_plugin=dev_plugin
    ):
        try:
            active = active_hypothesis(text)
        except AmbiguousMarkerError as exc:
            return _deny(str(exc), verdict_of="ambiguous-marker")
        if not active:
            return _deny(
                "Phase 4 edits must be a probe of a named hypothesis: add a line "
                "`Working on: H<n>` pointing at an existing `### H<n>` block before touching "
                "production code.",
                verdict_of="phase4-no-hypothesis",
            )
        if active not in parse_hypotheses(text):
            return _deny(
                f"`Working on: {active}` points at a hypothesis that does not exist. Name one "
                "that does, so the edit is attributable.",
                verdict_of="phase4-unknown-hypothesis",
            )

    if table_form_attempt(text):
        return _deny(
            "The ledger writes `Attempt`/`Recheck` as a table row. Markers are whole lines: "
            "write `Attempt 3 consumed` and `Recheck H1 #2` on their own line. The gate reads "
            "line starts, so a table cell is invisible and the transition never opens.",
            verdict_of="table-form",
        )

    # Only the cap is checked here: numbering is validated on `advance`, because a
    # hook runs on every tool call and parsing the whole ledger each time would
    # slow each write for a rule that only matters when the phase changes.

    refusals, notes = _secret_findings_for_write(ledger, text, tool, args)
    if refusals:
        return _deny(refusals[0], verdict_of="secret-in-record")
    if notes:
        # A secret exists elsewhere but this write does not add one: invite, do not block. The
        # operator's ruling is that invariant 26 forbids writing a secret, not working near one.
        return _allow(
            "a secret already exists in the task and is not being written now: "
            + notes[0]
            + " It is reported, not refused (§10: writing is refused, access is not)."
        )

    drift = task_drift(ledger, text)
    if drift:
        return _allow(drift)

    return Verdict(allow=True)


#: Fields that carry the **whole** new file body. A fragment (`new_string`) is not here: a fragment
#: scanned alone misses a secret it introduces into surrounding text, which was measured as a real
#: bypass (`probes/f19_bypass_check.py`).
_NEW_TEXT_FIELDS: Final[tuple[str, ...]] = ("content", "newText", "file_text")


def _prospective_body(
    current: str, tool: str, args: dict[str, Any], target: Path, ledger: Path
) -> str | None:
    """The text the addressed file will contain after this call, or ``None`` when unknown.

    Invariant 26 judges a record for secrets. Judging the **current** file made a fix impossible:
    an edit that removes the offending line was refused because the scan read the line it was
    about to delete (TASK-086 F19, mechanism measured in `probes/ha_scan_reads_disk.py`).

    So the content about to be written is what gets scanned. When the payload does not carry it —
    an edit shape the gate cannot reconstruct — the caller falls back to the file on disk, which is
    stricter, not looser.

    Args:
        current: The addressed file's present contents.
        tool: Lowercased tool name.
        args: Tool arguments.
        target: File the call addresses.
        ledger: Active ledger, for resolving relative targets.

    Returns:
        The prospective contents, or ``None`` when they cannot be reconstructed.
    """
    if tool in _SHELL_TOOLS:
        # A shell write goes through redirection or a heredoc; the gate does not reconstruct the
        # result, and guessing it would be the silent answer §0 forbids.
        return None

    for field in _NEW_TEXT_FIELDS:
        value = args.get(field)
        if isinstance(value, str):
            return value

    # An edit carries a fragment, not a file. Scanning the fragment alone would miss a secret that
    # the edit *introduces* into context the fragment does not contain (measured: a `new_string`
    # with a secret was allowed because nothing applied it to the body). Apply it, or fall back.
    old = args.get("old_string") or args.get("oldStr")
    new = args.get("new_string") or args.get("newStr")
    if isinstance(old, str) and isinstance(new, str):
        if old in current:
            return current.replace(old, new, 1)
        return None
    return None


def _secret_findings_for_write(
    ledger: Path, text: str, tool: str, args: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Secrets the write would produce, and secrets that already sit elsewhere (§10, invariant 26).

    Invariant 26 forbids *writing* a secret. It does not forbid working while a secret exists
    somewhere else — operator's ruling 2026-11-28 15:02: "the goal is to forbid writing secrets,
    not to forbid access to files that hold them". The previous version scanned every sub-task file
    on every write, so one stale record blocked the whole run, including edits to unrelated files
    (`probes/secret_presence_vs_write.py`, measured).

    The two questions are therefore separated and answered differently:

    * the body **this call will write** → refusal, because that is the write of a secret;
    * something already on disk **elsewhere** → a note, so the finding is not silently dropped while
      the run keeps working.

    Args:
        ledger: Ledger file.
        text: Ledger contents as they are on disk.
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        ``(refusals, notes)``. ``refusals`` is empty when the write introduces nothing.
    """
    target = _target_path(args)
    addressed: Path | None = None
    if target:
        candidate = Path(target).expanduser()
        addressed = candidate if candidate.is_absolute() else _project_of(ledger) / candidate
        addressed = _normalized(addressed)

    def describe(where: str, finding: secrets.Finding) -> str:
        """One finding as a sentence naming the place and the rule.

        Args:
            where: File name the finding sits in.
            finding: The scanner finding.

        Returns:
            The sentence.
        """
        return (
            f"secret in {where} at {secrets.describe(finding)}. Values, lengths, hashes and "
            "prefixes all help to recover it and are refused too (§10)."
        )

    refusals: list[str] = []
    notes: list[str] = []
    replaced = False
    bodies: list[tuple[Path, str]] = [(_normalized(ledger), text)]
    for path in subtask_files(ledger):
        bodies.append(
            (
                _normalized(path),
                path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "",
            )
        )

    for key, body in bodies:
        is_target = addressed is not None and key == addressed
        if is_target:
            prospective = _prospective_body(body, tool, args, key, ledger)
            if prospective is not None:
                body = prospective
                replaced = True
        for finding in secrets.scan(body):
            if is_target:
                refusals.append(describe(key.name, finding))
            else:
                notes.append(describe(key.name, finding))

    # A file that does not exist yet is not in `subtask_files` — it lists what is on disk. So
    # creating one was never scanned: measured, `Write` of a record with a secret to a new
    # `TASK-<n>/ST-1.md` was allowed while the same write to an existing file was refused.
    #
    # The scan is bounded to the run's own records, and that bound is a fix, not a courtesy
    # (TASK-086 ST-12, measured). A norm has to quote the forms it forbids — the spec's own §10
    # table carries `api_key: len=51` — so scanning any addressed file made the document that
    # defines a rule uneditable by the rule: every `StrReplace` on the spec was refused with
    # `secret in bug-fixer-v3-spec.md at line 1007`, whatever it changed. Invariant 26 is about
    # records (`### F<n>`, findings, log output), and those live in the ledger and its sub-files.
    known = {key for key, _ in bodies}
    if addressed is not None and not replaced and addressed not in known:
        if _is_record_file(addressed, ledger):
            prospective = _prospective_body("", tool, args, addressed, ledger)
            if prospective is not None:
                for finding in secrets.scan(prospective):
                    refusals.append(describe(addressed.name, finding))

    return refusals, notes


def _is_record_file(path: Path, ledger: Path) -> bool:
    """Whether a new file would be one of the run's own records (invariant 26).

    The secret rule judges records — the ledger, its sub-task files, findings, log output — not
    every document in the project. Measured reason for the bound: a norm quotes the forms it
    forbids, so a scan of any addressed file refused every edit to the spec, including the edit
    that fixes the norm (`probes/st12_norm_shapes.py`, TASK-086 ST-12).

    A path qualifies when it lives under the ledger's sub-task directory, or is a markdown file
    beside it — where §5.3a puts findings and notes. The ledger itself needs no branch here: it is
    always among the scanned bodies, so a call that addresses it never reaches this function.

    Args:
        path: File the call addresses.
        ledger: Ledger being enforced.

    Returns:
        ``True`` when the file would hold a record.
    """
    resolved = _normalized(path)
    folder = _normalized(subtask_directory(ledger))
    if resolved == folder or folder in resolved.parents:
        return True
    return resolved.parent == _normalized(ledger).parent and resolved.suffix == ".md"


def phase_gate_enabled(ledger_text: str) -> bool:
    """Whether the Phase-1 write ban applies to this ledger.

    Tooling tasks (building the guard itself, refactors) are not bug-fix runs, so
    the Phase-1 "tests only" rule would lock them out of their own files. They opt
    out with an anchored `Phase gate: off` line. The ban is on by default: an
    opt-out has to be written deliberately, in a line someone can grep.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        ``False`` only when the ledger opts out.
    """
    return _PHASE_GATE_OFF_RE.search(ledger_text) is None


def _addresses_ledger(detail: str, ledger: Path) -> bool:
    """Whether a write target names the ledger itself.

    Invariant 31 makes the task file writable in every phase: §5.3 puts ``### H<n>`` and
    ``### F<n>`` there, and a gate that refuses them forbids the work. The check is by
    resolved path, not by name — two ``TASK-999.md`` files in different directories are
    different files, and matching on the name alone would open the ban on the wrong one.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.

    Returns:
        ``True`` when every write target the call names is the ledger.
    """
    raw = Path(detail).expanduser()
    target = ledger.resolve()
    if raw.is_absolute():
        return raw.resolve() == target
    # A relative path can be relative to the harness's cwd, the ledger's directory or
    # any directory above it (``tasks/TASK-999.md`` from the project root). Every one
    # of those bases names the same file when it resolves to it.
    for base in (Path.cwd(), ledger.parent, *ledger.parents):
        if (base / raw).resolve() == target:
            return True
    return False


def _shell_write_targets(command: str) -> list[str]:
    """Destinations a shell command writes to.

    String search over the whole command cannot tell a write target from an argument,
    and removing the ledger's spelling leaves a dangling ``>>`` that still looks like a
    redirection. The targets are therefore read out: redirections, plus the arguments of
    the commands in ``_WRITE_COMMAND_RE`` that take a path.

    Args:
        command: Shell command line.

    Returns:
        Candidate destination paths, in command order; empty when none is named.
    """
    targets = [match.group(1) for match in _REDIRECT_TARGET_RE.finditer(_syntax_only(command))]
    for match in _WRITE_TARGET_COMMANDS_RE.finditer(_syntax_only(command)):
        # A write verb is syntax, so it is found in the unquoted skeleton; its ARGUMENTS must
        # be sliced from the original, or a quoted path loses its quoting. `shlex` is used
        # rather than `str.split`, because a path may contain a space: `rm -f "my file.py"`
        # is one argument, and splitting it produced two phantom targets.
        segment = command[match.start(1) : match.end(1)]
        try:
            tokens = shlex.split(segment)
        except ValueError:
            # An unbalanced quote cannot be tokenised; falling back to splitting is what the
            # code did before, and it is still better than losing the target entirely.
            tokens = segment.split()
        for token in tokens:
            if token.startswith("-"):
                continue
            targets.append(token[3:] if token.startswith("of=") else token)
    return targets


def _addresses_ledger(detail: str, ledger: Path) -> bool:
    """Whether a write target names the ledger itself.

    Invariant 31 makes the task file writable in every phase: §5.3 puts ``### H<n>`` and
    ``### F<n>`` there, and a gate that refuses them forbids the work the skill exists to
    do. The check is by resolved path, not by name — two ``TASK-999.md`` files in
    different directories are different files, and a name match would open the ban on the
    wrong one.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.

    Returns:
        ``True`` when the target is the ledger.
    """
    return _names_path(detail, ledger)


def _names_path(detail: str, target: Path) -> bool:
    """Whether a path or shell target names ``target``, resolved.

    Args:
        detail: Path or shell token.
        target: File the caller is asking about.

    Returns:
        ``True`` when both name the same file.
    """
    raw = Path(detail).expanduser()
    resolved = target.resolve()
    if raw.is_absolute():
        return raw.resolve() == resolved
    # A relative path can be relative to the harness's cwd, the target's directory or a
    # directory above it (``tasks/TASK-999.md`` from the project root). Every one of those
    # bases names the same file when it resolves to it.
    for base in (Path.cwd(), target.parent, *target.parents):
        if (base / raw).resolve() == resolved:
            return True
    return False


def _is_journal_write(detail: str, ledger: Path, tool: str) -> bool:
    """Whether a call writes, truncates or removes the run journal.

    Invariant 33: the journal belongs to the runtime. A shell command is judged on its
    targets, so ``cat TASK-999.md.bugfixer.jsonl`` — a read — stays allowed while
    ``rm`` or ``>>`` on the same file does not.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.
        tool: Lowercased tool name.

    Returns:
        ``True`` when the call targets the journal.
    """
    journal_path = journal.journal_path(ledger)
    if tool in _SHELL_TOOLS:
        return any(_names_path(target, journal_path) for target in _shell_write_targets(detail))
    return _names_path(detail, journal_path)


def _require_test_paths(ledger: Path, tool: str) -> list[str]:
    """Read the contract's test paths, failing closed when it names none.

    Args:
        ledger: Ledger being enforced.
        tool: Lowercased tool name for the diagnostic.

    Returns:
        Path prefixes considered tests.
    """
    tests = _test_paths(ledger)
    if not tests:
        _fail_closed(
            "no 'Test paths:' entry in bug-fixer.project.md, so this "
            f"{tool} call cannot be shown to be a test edit"
        )
    return tests


#: File extensions that hold prose and data, never behaviour (invariant 46). These are writable in
#: every phase. Enumerating the *text* formats is shorter and safer than enumerating every source
#: language: a language nobody listed would be silently writable, while a text format nobody listed
#: is merely refused.
_DOC_SUFFIXES: Final[tuple[str, ...]] = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".adoc",
    ".asciidoc",
    ".org",
    ".log",
    ".csv",
    ".tsv",
)

#: The one text file whose write is a *claim* about the change rather than prose about it, and is
#: therefore gated by evidence instead of by path (invariant 49, §11a).
CHANGELOG_NAME: Final[str] = "CHANGELOG.md"

#: Files the installer and `init` write. Without them the plugin does not install at all, so they
#: are exempt in every phase, not only while a contract is missing (invariant 48).
PLUGIN_FILES: Final[frozenset[str]] = frozenset(
    {
        "bug-fixer.project.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".gitignore",
    }
)

#: Path fragments of the harness wiring: the rules file the plugin owns and the hook config it
#: merges into. These are installation, so they are exempt alongside `PLUGIN_FILES`.
_PLUGIN_WIRING_MARKERS: Final[tuple[tuple[str, ...], ...]] = (
    (".cursor", "rules"),
    (".cursor", "hooks.json"),
    (".claude", "settings.json"),
    (".codex", "hooks.json"),
)

#: The skill tree itself. Invariant 47: during a run this is code — the same as the project's own —
#: and editing it needs `bugfixer --dev-plugin`, because a run that can rewrite its own guard has
#: no guard.
_SKILL_TREE_MARKERS: Final[tuple[tuple[str, ...], ...]] = (("skills", "bug-fixer"),)


def _matches_suffix(path: str, suffixes: tuple[str, ...]) -> bool:
    """Whether a path ends with one of these suffixes.

    Args:
        path: Path as written.
        suffixes: Lowercase suffixes including the dot.

    Returns:
        ``True`` when the suffix matches.
    """
    return path.lower().endswith(suffixes)


def _matches_markers(parts: tuple[str, ...], markers: tuple[tuple[str, ...], ...]) -> bool:
    """Whether a path's parts contain one of these ordered marker sequences.

    Args:
        parts: Path components.
        markers: Sequences that must appear consecutively.

    Returns:
        ``True`` on a match.
    """
    for marker in markers:
        width = len(marker)
        for index in range(len(parts) - width + 1):
            if parts[index : index + width] == marker:
                return True
    return False


def _is_plugin_file(path: str, root: Path, *, dev_plugin: bool = False) -> bool:
    """Whether a path is a file the plugin itself writes.

    Args:
        path: Path as written, absolute or relative to the project.
        root: Project directory.
        dev_plugin: When true, the skill tree counts as a plugin file too — the documented way to
            develop the plugin with its own gate installed (invariant 47).

    Returns:
        ``True`` for the contract, the rule files, the harness configs, and — only with
        ``dev_plugin`` — the skill tree.
    """
    candidate = Path(path.strip().strip("\"'`")).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.name in PLUGIN_FILES:
        return True
    parts = candidate.parts
    if _matches_markers(parts, _PLUGIN_WIRING_MARKERS):
        return True
    return dev_plugin and _matches_markers(parts, _SKILL_TREE_MARKERS)


def _is_doc_write(detail: str, ledger: Path, root: Path, tool: str) -> bool:
    """Whether every target of this write is prose rather than behaviour.

    Spec §5.5 and invariant 46: `.md`, `.txt`, `.log` and the rest are data. The one exception is
    `CHANGELOG.md`, which is a claim about the change and is judged by evidence (§11a) instead.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.
        root: Project directory.
        tool: Lowercased tool name.

    Returns:
        ``True`` when all targets are documentation and none is the changelog.
    """
    targets = _shell_write_targets(detail) if tool in _SHELL_TOOLS else [detail]
    if not targets:
        return False
    for target in targets:
        cleaned = target.strip().strip("\"'`")
        if Path(cleaned).name.upper() == CHANGELOG_NAME.upper():
            return False
        if not _matches_suffix(cleaned, _DOC_SUFFIXES):
            return False
    return True


def _is_raw_dump_write(detail: str, ledger: Path, tool: str) -> bool:
    """Whether the write goes to the project's raw-dump directory, or to `logs/`.

    Invariant 45: logs are how phases 1–3 show their evidence, and §5.3 point 2 names the dump
    directory as the place for output too large for the task file. Refusing it was a gate defect:
    the agent had nowhere to put the full test output the spec tells it to record.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.
        tool: Lowercased tool name.

    Returns:
        ``True`` when every target looks like a log destination.
    """
    targets = _shell_write_targets(detail) if tool in _SHELL_TOOLS else [detail]
    if not targets:
        return False
    dumps = _raw_dump_paths(ledger)
    for target in targets:
        cleaned = target.strip().strip("\"'`")
        if "logs" in Path(cleaned).parts:
            continue
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = _project_of(ledger) / candidate
        resolved = _normalized(candidate)
        if any(resolved == dump or dump in resolved.parents for dump in dumps):
            continue
        return False
    return True


def _raw_dump_paths(ledger: Path) -> list[Path]:
    """The raw-dump directories the contract names, resolved.

    Args:
        ledger: Ledger being enforced.

    Returns:
        Absolute directories; empty when the contract names none.
    """
    root = _project_of(ledger)
    for base in [ledger.parent, *ledger.parents]:
        contract = base / CONTRACT_NAME_HINT
        if not contract.is_file():
            continue
        text = contract.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"^Raw dumps(?:\s+too large for the ledger)?:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE
        )
        if not match:
            return []
        return [
            _normalized(root / item.strip().strip("`"))
            for item in match.group(1).split(",")
            if item.strip() and not item.strip().startswith("<")
        ]
    return []


def _is_changelog_write(detail: str, tool: str) -> bool:
    """Whether this call writes the project's `CHANGELOG.md`.

    Args:
        detail: Path or command from the classified call.
        tool: Lowercased tool name.

    Returns:
        ``True`` when any target is the changelog.
    """
    targets = _shell_write_targets(detail) if tool in _SHELL_TOOLS else [detail]
    return any(
        Path(target.strip().strip("\"'`")).name.upper() == CHANGELOG_NAME.upper()
        for target in targets
    )


def _changelog_problem(args: dict[str, Any], ledger: Path) -> str:
    """Check that a `CHANGELOG.md` write is backed by evidence (§11a, invariant 49).

    Three conditions, all required, because the entry exists to tell a reader "this is done and
    proved": the run reached a verdict, the entry states its changed-lines coverage, and that
    number meets the contract's threshold. Without a coverage command the plugin cannot verify a
    `tested:` claim, and saying so is better than accepting the number on trust (§0).

    Args:
        args: Tool arguments, which carry the entry's text for a write tool.
        ledger: Ledger being enforced.

    Returns:
        A refusal message, or an empty string when the claim is supported.
    """
    contract_text = _contract_text(ledger)
    threshold = contract_module.min_coverage(contract_text)
    command = contract_module.coverage_command(contract_text)

    content = args.get("content") or args.get("new_string") or args.get("newStr") or ""
    text = ledger.read_text(encoding="utf-8", errors="replace")
    # A run with a secret still on disk cannot be declared finished: writing is what invariant 26
    # forbids, and a changelog entry is the statement "this change is done". Ordinary work stays
    # open (operator's ruling 2026-11-28 15:02: access is not refused), but the finish is.
    lingering = secret_problems(ledger, text)
    if lingering:
        return (
            "the task still holds a secret, so the change cannot be declared finished: "
            + lingering[0]
            + " Remove it first — writing the entry would record a completed change beside an "
            "unresolved leak (§10, invariant 26)."
        )
    if not re.search(r"^\*\*Verdict\.\*\*", text, re.MULTILINE):
        return (
            "`CHANGELOG.md` is written only after a probe reached a verdict (spec §11a): the "
            "entry says a change is finished, and there is no verdict in the task file yet. "
            "Finish phase 4, record `**Verdict.**`, then write the entry."
        )
    # Two independent conditions (invariant 49). The threshold applies to *coverage*; the run
    # itself has to be green in full — "75% of tests passed" is a failing suite, not a green one.
    green_problem = _green_run_problem(text)
    if green_problem:
        return green_problem
    if not isinstance(content, str) or not content:
        # An edit that does not carry the new text cannot be checked; the gate says so rather
        # than assuming the entry is fine.
        return (
            "this edit to `CHANGELOG.md` does not carry the new text, so its `tested:` claim "
            "cannot be read. Use a write that includes the content (spec §11a)."
        )
    match = re.search(r"tested:\s*(\d{1,3})\s*%", content)
    if not match:
        # §11a: a change that touches no measurable line has nothing to measure, so the entry
        # names what did change instead of inventing a percentage. `None` means no such claim
        # was made, and then the number is still required.
        alternative = _counted_claim_problem(content, ledger)
        if alternative is None:
            return (
                f"`CHANGELOG.md` entries must state their coverage as `tested: <n>%` (spec "
                f"§11a). The threshold for this project is {threshold}%, and an entry without "
                "the number tells a reader nothing about whether the change is covered."
            )
        return alternative
    reported = int(match.group(1))
    if reported < threshold:
        return (
            f"`tested: {reported}%` is below this project's threshold of {threshold}%. The "
            "change is not covered enough to be recorded as finished: write the tests first — "
            "they are supposed to exist before the code (TDD, step 1.2) — then record the new "
            "number. A run that cannot reach the threshold returns to the probe, not to the "
            "changelog."
        )
    if not command:
        return (
            f"`tested: {reported}%` cannot be verified: the contract names no "
            "`Coverage command:`. Add one, or the number is a claim nobody checked (spec §0)."
        )
    return ""


def _docs_only_problem(content: str, ledger: Path) -> str | None:
    """Judge a ``docs_changed:`` or ``tests_changed:`` claim (§11a), or ``None``.

    Two changes have nothing to measure, for opposite reasons: documentation has no executable
    line, and a test *is* the instrument rather than the thing measured. Both name their files
    instead of inventing a percentage, and both are checked the same way, so one reader serves
    both — a second copy would drift from this one.

    Checks that need no git access (§12): the count matches the list, every listed path has the
    extension its form allows, and every listed path exists. ``tests_changed:`` gets one more,
    which does use the contract: every path is under ``Test paths:``, because a suffix cannot
    tell a test from production code.

    Args:
        content: The entry text being written.
        ledger: Ledger being enforced; its project resolves the listed paths.

    Returns:
        A refusal message, or ``None`` when no such claim is present.
    """
    return _counted_claim_problem(content, ledger)


def _counted_claim_problem(content: str, ledger: Path) -> str | None:
    """Judge a ``docs_changed:``/``tests_changed:`` claim.

    Args:
        content: The entry text being written.
        ledger: Ledger being enforced.

    Returns:
        A refusal message (possibly empty when the claim holds), or ``None`` when the entry makes
        no such claim and the caller should demand ordinary coverage instead.
    """
    for field, allowed, what in (
        ("docs_changed", _DOC_SUFFIXES, "documentation"),
        ("tests_changed", (".py", ".pyi"), "test files"),
    ):
        declaration = re.search(rf"{field}:\s*(\d+)\s*files", content)
        if declaration is None:
            continue
        problem = _counted_change_problem(content, ledger, field, allowed, what, declaration)
        if problem:
            return problem
        if field == "tests_changed":
            tests = _test_paths(ledger)
            outside = [
                path
                for path in _listed_paths(content, declaration)
                if not any(path.replace("\\", "/").startswith(prefix) for prefix in tests)
            ]
            if outside:
                return (
                    f"`tests_changed:` names {outside[0]!r}, which is not under this project's "
                    "`Test paths:`. The form is only for changes to test files: with production "
                    "code changed the entry must carry `tested: <n>%` (spec §11a)."
                )
        return ""
    return None


def _listed_paths(content: str, declaration: re.Match[str]) -> list[str]:
    """The bulleted paths that follow a ``<field>: <n> files`` declaration.

    Args:
        content: The entry text being written.
        declaration: Match of the count line.

    Returns:
        Paths in the order written, with surrounding backticks and quotes removed.
    """
    listed: list[str] = []
    for line in content[declaration.end() :].splitlines():
        item = re.match(r"^[-*]\s+(.+)$", line.strip())
        if item is None:
            if listed:
                break
            continue
        listed.append(item.group(1).strip().strip("`").strip("\"'"))
    return listed


def _counted_change_problem(
    content: str,
    ledger: Path,
    field: str,
    allowed: tuple[str, ...],
    what: str,
    declaration: re.Match[str],
) -> str:
    """Check one ``<field>: <n> files`` claim against its own list of paths.

    Args:
        content: The entry text being written.
        ledger: Ledger being enforced.
        field: The marker this claim uses, e.g. ``docs_changed``.
        allowed: Suffixes the claim may name.
        what: Human name of the category, for the refusal.
        declaration: Match of the count line.

    Returns:
        A refusal message, or an empty string when this claim holds.
    """
    declared = int(declaration.group(1))
    listed = _listed_paths(content, declaration)

    if not listed:
        return (
            f"`{field}:` must list the files it counts, one path per line (spec §11a): a number "
            "alone cannot be checked by anyone, and the list is the whole point of the form. "
            "Add the paths indented under the count."
        )
    if declared != len(listed):
        return (
            f"`{field}: {declared} files` lists {len(listed)} path(s). The count and the list "
            "must agree, or the entry tells a reader two different things (spec §11a)."
        )

    wrong_kind = [path for path in listed if Path(path).suffix.lower() not in allowed]
    if wrong_kind:
        return (
            f"`{field}:` names {wrong_kind[0]!r}, which is not {what}. This form is only for a "
            "change that touches no measurable line: with production code changed the entry must "
            "carry `tested: <n>%` and meet the threshold (spec §11a)."
        )
    root = _project_of(ledger)
    missing = [path for path in listed if not (root / path).is_file()]
    if missing:
        return (
            f"`{field}:` names {missing[0]!r}, which does not exist in the project. A path "
            "nobody can find is not evidence that a file changed (spec §11a)."
        )
    return ""


def _green_run_problem(text: str) -> str:
    """Check that the last full-suite run was green in every test (invariant 49, §11a).

    Two markers, both already written by the workflow (`ФАКТ (GREEN):` at step 4.7, and
    `ФАКТ (регрессия):` at 4.8a), so this adds no new obligation to the agent — it only stops the
    claim from being made when the evidence contradicts it.

    The distinction that matters: the coverage threshold is a number about *lines*, and this is
    about the run as a whole. A suite where 75% of tests pass is a failing suite, and a changelog
    entry saying the change is done would be false.

    Args:
        text: Ledger contents.

    Returns:
        A refusal message, or an empty string when the last run was fully green.
    """
    green = [match for match in re.finditer(r"^.*ФАКТ \(GREEN\):.*$", text, re.MULTILINE)]
    if not green:
        return (
            "`CHANGELOG.md` needs a green full-suite run first (spec §11a, §9): record "
            "`ФАКТ (GREEN):` at step 4.7 with the command and its result. The entry claims the "
            "change works, and no run proves it yet."
        )
    last = green[-1]
    result = last.group(0)
    failed = re.search(
        r"\b(\d+\s+)?(failed|failure|failures|failing|error|errors)\b", result, re.IGNORECASE
    )
    if failed:
        return (
            "the last `ФАКТ (GREEN):` records a failing or erroring run ("
            f"`{result.strip()[:120]}`). Every test has to pass before a changelog entry: the "
            "75% threshold is about *coverage of changed lines*, not about the share of tests "
            "that passed. Fix the suite, then record the new run."
        )
    # A regression recorded after the green run means the baseline broke later: the green marker
    # is then stale evidence about a tree that no longer exists.
    after = text[last.end() :]
    if re.search(r"^.*ФАКТ \(регрессия\):.*$", after, re.MULTILINE):
        return (
            "a `ФАКТ (регрессия):` was recorded after the last green run, so the baseline broke "
            "and that green marker describes a tree that no longer exists (spec §9: no "
            "baseline-green test may fail). Fix the regression and run the full suite again."
        )
    return ""


def _contract_text(ledger: Path) -> str:
    """The project contract's contents, or an empty string when there is none.

    Args:
        ledger: Ledger being enforced.

    Returns:
        Contract text, empty when unreadable.
    """
    for base in [ledger.parent, *ledger.parents]:
        candidate = base / CONTRACT_NAME_HINT
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
    return ""


def _is_code_write(detail: str, ledger: Path, tool: str, *, dev_plugin: bool = False) -> bool:
    """Decide whether a write changes behaviour, and so needs a hypothesis.

    The rule is an explicit allow-list of what a run may write (§5.5), not "outside Test paths:".
    That inference was the defect: it made logs, reports and documentation look like production
    code, so a run could not record the evidence it exists to produce.

    Order matters and is deliberate:

    1. the ledger — allowed in every phase (invariant 31);
    2. the plugin's own files — allowed in every phase (invariant 48);
    3. the raw-dump directory and `logs/` — allowed in every phase (invariant 45);
    4. prose and data — allowed in every phase (invariant 46);
    5. tests — allowed in every phase: they are the measuring instrument (§1, step 1.2);
    6. everything else — code, judged by the phase.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.
        tool: Lowercased tool name for the diagnostic.
        dev_plugin: When true, the skill tree is treated as a plugin file: the documented way to
            develop the plugin with its own gate installed (invariant 47).

    Returns:
        ``True`` when the write changes behaviour.
    """
    if not detail:
        # A write with no identifiable target cannot be shown to be harmless.
        return True

    if tool not in _SHELL_TOOLS and _addresses_ledger(detail, ledger):
        return False
    root = _project_of(ledger)
    if _is_plugin_file(detail, root, dev_plugin=dev_plugin):
        return False
    if _is_raw_dump_write(detail, ledger, tool):
        return False
    if _is_doc_write(detail, ledger, root, tool):
        return False

    targets = _shell_write_targets(detail) if tool in _SHELL_TOOLS else [detail]
    if not targets:
        # A write with no readable destination cannot be shown to be harmless.
        return True
    if all(_addresses_ledger(target, ledger) for target in targets):
        return False
    # §5.6: a NEW file under scripts/ or probes/ is the exit from the inline-script ban, so it is
    # allowed in every phase. An existing one is a shipped tool and stays code (checked per
    # target, because a command line may name several).
    if all(_is_new_script_target(target, ledger) for target in targets):
        return False
    tests = _require_test_paths(ledger, tool)
    for target in targets:
        normalized = target.replace("\\", "/")
        if _addresses_ledger(target, ledger):
            continue
        # Invariant 47 names the tree, not the tool that edits it: a shell write whose every
        # target is the plugin is the same act as a `Write` to it. Checked per target because a
        # command line is not a path, so a marker search over the whole string never matched.
        if _is_plugin_file(target, root, dev_plugin=dev_plugin):
            continue
        if any(normalized.startswith(prefix) or f"/{prefix}" in normalized for prefix in tests):
            continue
        return True
    return False


def _outside_project_targets(detail: str, ledger: Path, tool: str) -> list[str]:
    """Write targets that fall outside the project the ledger belongs to.

    Spec invariant 43: the plugin works inside the open project and its subdirectories, and
    nowhere else. System directories are refused unconditionally — in every phase, including the
    probe phase — because a fix belongs to the project, and a temporary file belongs in the
    project's own dump directory rather than in `/tmp`. A person who needs a system file changed
    does that themselves; the agent prints the command.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced; its project is the boundary.
        tool: Lowercased tool name.

    Returns:
        Targets outside the project, in command order. Empty when every target is inside.
    """
    root = _project_of(ledger)
    targets = _shell_write_targets(detail) if tool in _SHELL_TOOLS else [detail]
    outside: list[str] = []
    for target in targets:
        # A target taken from a command line may carry the shell's quoting; the path is inside.
        cleaned = target.strip().strip("\"'`")
        # A relative target is relative to the project, which is inside by definition.
        candidate = Path(cleaned).expanduser()
        if not candidate.is_absolute():
            continue
        resolved = _normalized(candidate)
        if resolved == root or root in resolved.parents:
            continue
        outside.append(cleaned)
    return outside


def _project_of(ledger: Path) -> Path:
    """The project directory a ledger belongs to.

    The contract's own directory is the boundary, not the ledger's: a ledger may live in
    `tasks/` or in `docs/bugs/`, and the ban is about the project.

    Args:
        ledger: Ledger being enforced.

    Returns:
        Resolved project directory.
    """
    for base in [ledger.parent, *ledger.parents]:
        if (base / CONTRACT_NAME_HINT).is_file():
            return _normalized(base)
    return _normalized(ledger.parent)


def _normalized(path: Path) -> Path:
    """Resolve a path for comparison without requiring it to exist.

    `Path.resolve` is not usable alone: a write target usually does not exist yet, and on macOS
    a symlinked `/tmp` would compare unequal to `/private/tmp`. Resolving the parent chain and
    re-attaching the name keeps the comparison stable for both cases.

    Args:
        path: Path as written.

    Returns:
        Absolute, symlink-resolved path.
    """
    absolute = path if path.is_absolute() else Path.cwd() / path
    try:
        return absolute.resolve()
    except OSError:
        return absolute.parent.resolve() / absolute.name


def _unblocks_setup(tool: str, args: dict[str, Any], root: Path) -> bool:
    """Whether this call is setup work that a missing contract must not block.

    Two writes keep the refusal from becoming a lockout:

    * **the contract itself** — otherwise the gate demands a file and refuses the write that
      creates it;
    * **the plugin's own installed tree** — the documented install copies the skill into the
      project, and a project that has not been initialised yet is exactly where that happens.

    Everything else is refused: a gate that opened arbitrary writes "while setting up" would be
    the silent gate again under another name.

    Args:
        tool: Lowercased tool name.
        args: Tool arguments.
        root: Project directory.

    Returns:
        ``True`` when every write target is one of those two.
    """
    try:
        is_write, detail = _is_write_attempt(tool, args)
    except (UnknownToolError, UnreadableCommandError):
        return False
    if not is_write or not detail:
        return False

    targets = _shell_write_targets(detail) if tool in _SHELL_TOOLS else [detail]
    if not targets:
        return False
    return all(_is_setup_target(target, root) for target in targets)


def _is_setup_target(target: str, root: Path) -> bool:
    """Whether one write target is the contract or the plugin's installed tree.

    Args:
        target: Path from the tool call, absolute or relative to the project.
        root: Project directory.

    Returns:
        ``True`` when the target is setup rather than work.
    """
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = root / path
    parts = path.parts

    if path.name == CONTRACT_NAME_HINT:
        return True
    # The installer writes into a skills directory of its own choosing per harness; matching the
    # directory names keeps this independent of which harness is being installed for.
    if "skills" in parts and "bug-fixer" in parts:
        return True
    # Creating the ledger itself: the contract names where findings go, and until that file
    # exists there is no run to enforce. Only a path matching the contract's own pattern counts,
    # so this cannot be used to write arbitrary files in the project.
    contract = _find_contract(root)
    if contract is None:
        return False
    pattern = _contract_ledger_pattern(contract)
    if not pattern:
        return False
    return _matches_ledger_pattern(path, pattern)


def _matches_ledger_pattern(path: Path, pattern: str) -> bool:
    """Whether a path is the file the contract names as its ledger.

    Args:
        path: Candidate file, absolute.
        pattern: Glob from the contract's ``Task/bug file:``.

    Returns:
        ``True`` when the name matches the glob and the directory is where the pattern points.
    """
    from fnmatch import fnmatch  # noqa: PLC0415 — one call site

    if not fnmatch(path.name, Path(pattern).name):
        return False
    pattern_dir = Path(pattern).parent
    if not pattern_dir.parts:
        return True
    return pattern_dir.as_posix() in path.as_posix()


def _contract_placeholders(root: Path) -> list[str]:
    """Problems in the contract's *content*, not in finding its ledger.

    Two kinds, and both mean the contract cannot be followed as written:

    * a required field is still a ``<…>`` placeholder — the agent would be obeying a template;
    * the contract was never verified — its values were derived from the filesystem, so the test
      command may not exist and the search tools may not be callable. Phase 1 would then record a
      baseline from a command that fails to start.

    A contract written before the marker existed counts as unverified: assuming its absence means
    "trusted" is a guess, and §0 forbids guesses.

    Args:
        root: Project directory.

    Returns:
        Refusal messages, empty when the contract is complete and confirmed.
    """
    contract = _find_contract(root)
    if contract is None:
        return []
    text = contract.read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []
    for name in ("Full suite", "Test paths", "Task/bug file", "Search"):
        match = re.search(
            rf"^[ \t>*-]*{re.escape(name)}[ \t]*:[ \t]*(.+)$", text, re.MULTILINE | re.IGNORECASE
        )
        if match is None:
            continue
        if match.group(1).strip().startswith("<"):
            problems.append(
                f"`{name}:` in {contract.name} is still a template placeholder "
                f"({match.group(1).strip()}). "
            )
    if problems:
        return problems

    verified = contract_module.verified_by(text)
    if not contract_module.is_verified(text):
        state = "missing" if not verified else f"`{verified}`"
        return [
            f"{contract.name} has not been verified: `Contract verified by:` is {state}. Its "
            "values were derived from the filesystem, so nobody has run the test command or "
            "checked that the search tools exist. Follow `references/init.md` — run the command, "
            "confirm the tool names — then replace that line with who confirmed it and when. "
        ]

    if not contract_module.has_suite(text):
        # The contract is valid and confirmed, but the project has no suite. §1 needs a baseline
        # and §6 makes the command mandatory, so this stops the run while the user can still
        # choose — after a failing test is written, the same discovery costs an attempt.
        return [
            "`Full suite:` is `none`: this project has no full test suite, so a run cannot record "
            "a baseline (step 1.4) or compare before and after (step 4.7). Configure the suite "
            "and verify the contract again. Tell the user, and offer these three options, best "
            "first: (1) add a test suite — propose three concrete options for this project "
            "(framework, first test, command) before they choose; (2) if a suite exists under a "
            "command `bugfixer init` could not detect, put it in `Full suite:` and run it once; "
            "(3) if a suite cannot be added now, do not use this plugin for this bug — a run "
            "without a baseline cannot prove it fixed anything. "
        ]
    return problems


def _read_event() -> dict[str, Any]:
    """Read the harness payload from stdin.

    Empty stdin is ``{}``: a manual run with no payload, which the caller may then
    treat as "no run active". Non-JSON on stdin is an error, not an empty event —
    swallowing it would let a corrupt payload read as "gate inactive" and allow
    every write in an active run.

    Returns:
        Parsed payload, or an empty dict when stdin is empty.

    Raises:
        UnreadablePayloadError: When stdin holds something that is not a JSON object.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UnreadablePayloadError(f"stdin is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise UnreadablePayloadError(f"stdin is a {type(parsed).__name__}, not a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 to allow, 2 to deny.
    """
    parser = argparse.ArgumentParser(
        prog="bugfixer_gate.py",
        description="Deny bug-fixer rule violations from an agent hook.",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help=(
            "ledger file; omit to discover it from the project contract's "
            "`Task/bug file:` pattern in the hook's working directory"
        ),
    )
    parser.add_argument(
        "--dev-plugin",
        action="store_true",
        help=(
            "allow edits to the plugin's own skill tree (skills/bug-fixer/**). For developing "
            "the plugin with its own gate installed; never part of a normal run (invariant 47)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        event = _read_event()
    except UnreadablePayloadError as exc:
        _fail_closed(f"cannot read the hook payload: {exc}")

    tool = _tool_name(event)
    tool_args = _tool_input(event)

    try:
        ledger = _resolve_ledger(event, args.ledger)
    except (ContractMissingError, LedgerNotFoundError) as exc:
        # A project that cannot be configured is not "no run under way": the plugin has been
        # wired here and cannot do its job, so it says why and stops (spec §0). Two writes stay
        # open, because refusing them would make the refusal a lockout: creating the contract,
        # and the installer writing its own tree.
        if _unblocks_setup(tool, tool_args, _project_root(event)):
            _emit(Verdict(allow=True))
            return EXIT_ALLOW
        # §5.6: environment setup — including dependency installation — happens during
        # initialisation, so these forms are open while the contract is still `not yet`.
        # See `_is_env_setup_window` for why the scope is the environment, not the linter alone.
        if _is_env_setup_window(tool, tool_args):
            _emit(Verdict(allow=True))
            return EXIT_ALLOW
        _fail_closed(str(exc))
    if args.ledger and ledger is None:
        # The adapter names a ledger explicitly, so a missing one is a broken
        # install, not "no run active". Treating it as inactive disables the gate
        # silently for the whole run.
        _fail_closed(
            f"--ledger {args.ledger} does not exist; the adapter is pointed at a file "
            "that is not there"
        )

    # A contract whose fields are still `<…>` is a contract nobody has read: the agent would be
    # following instructions that say "<e.g. make test>". Checked here rather than in discover,
    # because it is about the contract's content, not about finding the ledger.
    contract_problems = _contract_placeholders(_project_root(event))
    if contract_problems:
        if _unblocks_setup(tool, tool_args, _project_root(event)):
            _emit(Verdict(allow=True))
            return EXIT_ALLOW
        if _is_env_setup_window(tool, tool_args):
            _emit(Verdict(allow=True))
            return EXIT_ALLOW
        # The message already ends with what to do; appending a generic "fill it in" would fit
        # placeholders and mislead here, where the field is filled and the project is the problem.
        _fail_closed(contract_problems[0].rstrip())
    verdict = decide(ledger=ledger, tool=tool, args=tool_args, dev_plugin=args.dev_plugin)
    _emit(verdict)
    return EXIT_ALLOW if verdict.allow else EXIT_DENY


if __name__ == "__main__":
    raise SystemExit(main())
