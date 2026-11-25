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
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, NoReturn

# The hooks directory is not a package: the gate is executed by path from a
# harness, so its own directory must be importable for the sibling scanner.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bugfixer_secrets as secrets

#: Exit codes shared with every adapter.
EXIT_ALLOW: Final[int] = 0
EXIT_DENY: Final[int] = 2

#: Sidecar suffix, next to the ledger.
STATE_SUFFIX: Final[str] = ".bugfixer.json"

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
    r"^[ \t]*[-*][ \t]+(F\d+)[ \t]*→[ \t]*([A-Z][A-Z ]*)", re.MULTILINE
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

#: The ``## Report`` section (invariant 19).
_REPORT_RE: Final[re.Pattern[str]] = re.compile(r"^##[ \t]+Report[ \t]*$", re.MULTILINE)

#: The ``## Remaining`` section (invariant 28).
_REMAINING_RE: Final[re.Pattern[str]] = re.compile(r"^##[ \t]+Remaining[ \t]*$", re.MULTILINE)

#: A bullet under ``## Remaining``: a heading alone is not a handover.
_REMAINING_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"^[ \t]*[-*][ \t]+\S", re.MULTILINE)

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
_WRITE_COMMAND_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"rm|mv|cp|install|tee|touch|mkdir|rmdir|truncate|sed\s+-i|perl\s+-i|"
    r"dd\s+of=|chmod|chown|ln\s+-s|git\s+(?:apply|checkout|restore|reset|stash|commit)"
    r")\b"
    r"|>>?\s*[^&|\s]"
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

#: Harness tool names that provably cannot mutate the filesystem. Anything outside
#: all three sets is *unknown*, and unknown is not silently allowed: during an
#: active run it fails closed, because a harness renaming a tool must not turn the
#: gate into decoration. Adding a name here is a deliberate act.
_SAFE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "read",
        "readfile",
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
        "think",
        "ask",
        "askquestion",
    }
)

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
    """

    allow: bool
    reason: str = ""


@dataclass(frozen=True)
class RecordLink:
    """One entry of a hypothesis's ``#### Records`` list.

    Attributes:
        id: Record that produced the verdict, e.g. ``"F3"``.
        verdict: Verdict keyword as written, e.g. ``"REFUTED"``.
    """

    id: str
    verdict: str


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


def _deny(reason: str) -> Verdict:
    """Return a deny verdict with an actionable reason.

    Args:
        reason: What the agent must do instead.

    Returns:
        A denying :class:`Verdict`.
    """
    return Verdict(allow=False, reason=reason)


def _fail_closed(message: str) -> NoReturn:
    """Deny on an internal error that cannot be safely interpreted.

    Args:
        message: What went wrong and how to fix it.
    """
    verdict = _deny(f"{message} (gate failed closed; fix the state or stop the run)")
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
        return bool(_WRITE_COMMAND_RE.search(command)), command
    if tool in _WRITE_TOOLS:
        return True, _target_path(args)
    if tool in _SAFE_TOOLS:
        return False, ""
    raise UnknownToolError(
        f"tool {tool!r} is neither a known write tool, a known shell tool, nor a known "
        "read-only tool"
    )


# --- ledger ------------------------------------------------------------------


def _resolve_ledger(event: dict[str, Any], explicit: str | None) -> Path | None:
    """Find the ledger this run writes to.

    Args:
        event: Harness event, which may carry a path.
        explicit: ``--ledger`` value from the adapter.

    Returns:
        Path to an existing ledger, or ``None`` when no run is active.
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
    return None


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
        RecordLink(id=record_id, verdict=verdict.strip())
        for record_id, verdict in _RECORD_LINK_RE.findall(_records_section(block))
    ]


def derive_status(hypothesis: Hypothesis) -> str:
    """Derive a hypothesis status from its records.

    The ``Status`` table field is a display convenience and is never the source of
    truth: a field can be edited without doing the work, a record cannot.

    ``PARTIALLY`` and ``DEGRADED`` keep the hypothesis **unchecked** (``PROPOSED``):
    a partial hit is deepened by the same hypothesis, not retired, so it stays in
    the queue.

    Args:
        hypothesis: Parsed hypothesis.

    Returns:
        ``PROPOSED``, ``CONFIRMED``, or ``REFUTED``.
    """
    if not hypothesis.records:
        return "PROPOSED"
    verdict = hypothesis.records[-1].verdict.upper()
    if verdict.startswith("CONFIRMED"):
        return "CONFIRMED"
    if verdict.startswith("REFUTED"):
        return "REFUTED"
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
        Warning text when the task changed, else empty. Records the fingerprint on
        first sight, so the baseline is the first version ever read.
    """
    current = task_fingerprint(text)
    if not current:
        return ""
    state = read_sidecar(ledger)
    recorded = state.get("task_fingerprint")
    if recorded is None:
        # The sidecar is shared with the phase machine, which fails closed when
        # `phase` is missing. Recording a fingerprint must not make the gate
        # unreadable, so the default phase is written at the same time.
        state.setdefault("phase", 1)
        state.setdefault("status", "RUNNING")
        state.setdefault("transitions", [])
        state["task_fingerprint"] = current
        write_sidecar(ledger, state)
        return ""
    if recorded != current:
        return (
            "the `## Task` block no longer matches the text this run started with. "
            "Refinements belong in `## Clarifications`; if the task itself changed, say so "
            "in the report — a correct fix for a different bug is not a fix."
        )
    return ""


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
        for finding in secrets.blocking(secrets.scan(record.body)):
            problems.append(
                f"secret in `{record.id}` at {secrets.describe(finding)}. Remove the value: "
                "log its length, type or hash instead, and never the value itself."
            )
    return problems


def read_sidecar(ledger: Path) -> dict[str, Any]:
    """Read the sidecar, treating a missing file as empty state.

    Args:
        ledger: Ledger file.

    Returns:
        Parsed mapping, or an empty dict.

    Raises:
        SystemExit: When the sidecar exists but is unreadable — the gate must not
            guess, and overwriting it would hide the damage.
    """
    path = _state_path(ledger)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail_closed(f"cannot read bug-fixer state {path}: {exc}")
    if not isinstance(payload, dict):
        _fail_closed(f"bug-fixer state {path} is not a JSON object")
    return payload


def write_sidecar(ledger: Path, state: dict[str, Any]) -> None:
    """Write the sidecar, preserving fields this module does not own.

    Args:
        ledger: Ledger file.
        state: Full state mapping to persist.
    """
    _state_path(ledger).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


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
            if _REMAINING_ITEM_RE.search(section) is None:
                problems.append(
                    "`## Remaining` exists but lists nothing. An empty remainder means the work "
                    "is DONE; add at least one item or change the status."
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


def _state_path(ledger: Path) -> Path:
    """Return the sidecar path for a ledger.

    Args:
        ledger: Ledger file.

    Returns:
        Sidecar path next to the ledger.
    """
    return ledger.with_name(ledger.name + STATE_SUFFIX)


def read_phase(ledger: Path) -> int:
    """Read the current phase from the sidecar.

    Args:
        ledger: Ledger file.

    Returns:
        Phase number (1 to 5); ``PHASE 0`` is outside the automaton by design.

    Raises:
        SystemExit: When the sidecar exists but is unreadable or invalid — the
            gate must not guess a phase.
    """
    path = _state_path(ledger)
    if not path.is_file():
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail_closed(f"cannot read bug-fixer state {path}: {exc}")
    phase = payload.get("phase") if isinstance(payload, dict) else None
    if phase not in (1, 2, 3, 4, 5):
        _fail_closed(f"bug-fixer state {path} has invalid phase {phase!r}")
    return int(phase)


def _test_paths(ledger: Path) -> list[str]:
    """Read the project's test path prefixes from its contract.

    Two layouts exist and both must work:

    * ``bug-fixer.project.md`` in the project root — what the skill's Step 0
      creates for a new project;
    * ``repo-contract.md`` beside the skill — the filled instance a repository
      keeps in-tree (this repo uses it), which lives under ``skills/<id>/references``.

    Args:
        ledger: Ledger file, used to walk up toward the project root.

    Returns:
        Path prefixes considered tests. Empty when no contract states them.
    """
    roots = [ledger.parent, *ledger.parents]
    candidates = [base / "bug-fixer.project.md" for base in roots]
    candidates += [
        root / "repo-contract.md"
        for base in roots[:3]
        for root in list((base / "skills").glob("*/references")) + [base]
    ]

    for contract in candidates:
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


def decide(
    *,
    ledger: Path | None,
    tool: str,
    args: dict[str, Any],
) -> Verdict:
    """Decide whether a tool call is allowed.

    Args:
        ledger: Active ledger, or ``None`` when no bug-fixer run is under way.
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        The verdict for this call.
    """
    if ledger is None:
        return _allow("no bug-fixer ledger found; gate inactive")

    text = ledger.read_text(encoding="utf-8", errors="replace")
    if legacy_ledger(text):
        return _deny(
            "this ledger was started under bug-fixer 2.1 (it has `## Iteration N` and no "
            "`### H<n>` hypotheses). Finish it under 2.1, or start a new ledger for this bug: "
            "its history has no hypotheses, so the 3.0 guards would refuse every transition "
            "and the refusals would look like a broken gate."
        )

    try:
        is_write, detail = _is_write_attempt(tool, args)
    except UnknownToolError as exc:
        # An active run plus a call the gate cannot classify is a wiring defect,
        # not a read. Deny so the operator sees it on the first call.
        return _deny(
            f"{exc}. The gate cannot prove this call is safe, so it is denied. "
            "Either the harness renamed a tool (fix the adapter) or the payload "
            "shape changed (fix the gate)."
        )
    except UnreadableCommandError as exc:
        return _deny(
            f"{exc}. A shell call whose command cannot be read cannot be shown to be "
            "test-only, so it is denied during an active run."
        )

    if not is_write:
        return Verdict(allow=True)

    status = read_status(text)

    if status == "UNREADABLE":
        return _deny(
            "the ledger's `STATUS:` line is not one of RUNNING, DONE, PARTIAL, "
            "LIMIT_REACHED, BLOCKED. An unreadable status is not read as RUNNING."
        )

    # Invariants 19/28/29: a terminal status is written together with its evidence. The
    # field check runs *before* the status block so the refusal can name what is missing —
    # an agent told only "the run is over" cannot tell a forgotten field from a broken gate.
    if status in {"DONE", "PARTIAL", "LIMIT_REACHED", "BLOCKED"}:
        field_problems = terminal_status_problems(text, status)
        if field_problems:
            return _deny(
                field_problems[0] + " The run is over either way, so this is written down "
                "rather than fixed by editing code."
            )

    if status in {"DONE", "PARTIAL"}:
        return _deny(
            f"This bug is already marked STATUS: {status}. A partial run is finished, not "
            "paused: the remainder is listed for the next task, so editing code here would "
            "finish a run nobody is tracking. Start a new ledger."
        )
    if status == "LIMIT_REACHED":
        return _deny(
            f"This bug is marked STATUS: LIMIT_REACHED — the attempt cap ({MAX_ATTEMPTS}) is "
            "spent. Do not edit code: report, list every refuted hypothesis, and ask the user for "
            "artefacts."
        )
    if status == "BLOCKED":
        return _deny(
            "This bug is marked STATUS: BLOCKED. Do not edit code. Ask the user for the "
            "artefacts you listed and start a new run when they arrive."
        )

    # Invariant 3: at ``A = 10`` the only correct next action is ``LIMIT_REACHED``. This is
    # checked before the phase-4 marker rules, because a refused cap is not an instruction
    # about markers — telling the agent to add `Working on:` when the run is over is the
    # wrong next step.
    try:
        attempts = count_attempts(text)
    except CounterResetError as exc:
        return _deny(str(exc))
    if attempts >= MAX_ATTEMPTS:
        return _deny(
            f"Attempt cap reached ({attempts} of {MAX_ATTEMPTS}). Write "
            "`STATUS: LIMIT_REACHED` with `## Report` instead of another change."
        )

    phase = read_phase(ledger)
    if phase != 4 and phase_gate_enabled(text) and _is_code_write(detail, ledger, tool):
        return _deny(
            f"Phase {phase} forbids production-code changes: only Phase 4 (probe) may edit "
            "code, and only as a probe of a named hypothesis. Phases 1-3 collect evidence; "
            "Phase 5 judges it. Run `bugfixer_gate_state.py <ledger> advance 4 --edge "
            "FIRST_GUESS --evidence \"H0 created\"` (or HYPOTHESIS_READY) once the "
            "preconditions are written."
        )

    if phase == 4 and phase_gate_enabled(text) and _is_code_write(detail, ledger, tool):
        try:
            active = active_hypothesis(text)
        except AmbiguousMarkerError as exc:
            return _deny(str(exc))
        if not active:
            return _deny(
                "Phase 4 edits must be a probe of a named hypothesis: add a line "
                "`Working on: H<n>` pointing at an existing `### H<n>` block before touching "
                "production code."
            )
        if active not in parse_hypotheses(text):
            return _deny(
                f"`Working on: {active}` points at a hypothesis that does not exist. Name one "
                "that does, so the edit is attributable."
            )

    if table_form_attempt(text):
        return _deny(
            "The ledger writes `Attempt`/`Recheck` as a table row. Markers are whole lines: "
            "write `Attempt 3 consumed` and `Recheck H1 #2` on their own line. The gate reads "
            "line starts, so a table cell is invisible and the transition never opens."
        )

    # Only the cap is checked here: numbering is validated on `advance`, because a
    # hook runs on every tool call and parsing the whole ledger each time would
    # slow each write for a rule that only matters when the phase changes.

    for problem in scan_records(text):
        return _deny(problem)

    drift = task_drift(ledger, text)
    if drift:
        return _allow(drift)

    return Verdict(allow=True)


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


def _is_code_write(detail: str, ledger: Path, tool: str) -> bool:
    """Decide whether a write touches production code rather than tests.

    Args:
        detail: Path or command from the classified call.
        ledger: Ledger being enforced.
        tool: Lowercased tool name for the diagnostic.

    Returns:
        ``True`` when the write is outside the project's test paths.
    """
    if not detail:
        # A write with no identifiable target cannot be shown to be a test edit.
        return True

    tests = _test_paths(ledger)
    if not tests:
        _fail_closed(
            "no 'Test paths:' entry in bug-fixer.project.md, so this "
            f"{tool} call cannot be shown to be a test edit"
        )

    if tool not in _SHELL_TOOLS:
        normalized = detail.replace("\\", "/")
        return not any(normalized.startswith(prefix) or f"/{prefix}" in normalized for prefix in tests)

    # A shell write may name several files; allow only when every write target
    # looks like a test path.
    if _WRITE_COMMAND_RE.search(detail) and not any(prefix in detail for prefix in tests):
        return True
    return False


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
    parser.add_argument("--ledger", default=None, help="ledger file; overrides the payload")
    args = parser.parse_args(argv)

    try:
        event = _read_event()
    except UnreadablePayloadError as exc:
        _fail_closed(f"cannot read the hook payload: {exc}")

    ledger = _resolve_ledger(event, args.ledger)
    if args.ledger and ledger is None:
        # The adapter names a ledger explicitly, so a missing one is a broken
        # install, not "no run active". Treating it as inactive disables the gate
        # silently for the whole run.
        _fail_closed(
            f"--ledger {args.ledger} does not exist; the adapter is pointed at a file "
            "that is not there"
        )
    verdict = decide(ledger=ledger, tool=_tool_name(event), args=_tool_input(event))
    _emit(verdict)
    return EXIT_ALLOW if verdict.allow else EXIT_DENY


if __name__ == "__main__":
    raise SystemExit(main())
