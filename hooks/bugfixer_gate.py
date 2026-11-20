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
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn

#: Exit codes shared with every adapter.
EXIT_ALLOW: Final[int] = 0
EXIT_DENY: Final[int] = 2

#: Hard cap on complete passes through Phase 1 -> 2 -> 3.
MAX_ITERATIONS: Final[int] = 10

#: Sidecar suffix, next to the ledger.
STATE_SUFFIX: Final[str] = ".bugfixer.json"

#: Ledger heading that advances the iteration counter.
_ITERATION_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,4}\s*Iteration\s+(\d+)\b", re.MULTILINE)

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
    """
    for key in ("tool_input", "toolInput", "args", "arguments", "input"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
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
        Command string, or an empty string.
    """
    for key in ("command", "cmd", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _is_write_attempt(tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Classify a tool call as a write or not.

    Args:
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        ``(True, path_or_command)`` when the call mutates the filesystem.
    """
    if tool in _SHELL_TOOLS:
        command = _shell_command(args)
        return bool(command and _WRITE_COMMAND_RE.search(command)), command
    if tool in _WRITE_TOOLS:
        return True, _target_path(args)
    return False, ""


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


def count_iterations(ledger_text: str) -> int:
    """Count completed iterations in a ledger.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        The highest ``## Iteration N`` number seen, or ``0``.
    """
    numbers = [int(match.group(1)) for match in _ITERATION_RE.finditer(ledger_text)]
    return max(numbers) if numbers else 0


def read_status(ledger_text: str) -> str:
    """Read the run status from a dedicated status line in the ledger.

    Only a line that *starts* with ``STATUS:`` (optionally after list or quote
    markers) counts. Prose that mentions the marker must not be mistaken for this
    run's status: TASK-078 says "STATUS: BLOCKED" inside its own rules table, and
    reading that as a live status would block a finished task forever.

    ``PARTIAL`` is a continuation, not a terminal state: the run goes back to
    Phase 2 to finish the listed remainder, so writes stay allowed. It is
    reported distinctly so a reader is never told a partial run is complete.

    Args:
        ledger_text: Full ledger contents.

    Returns:
        ``BLOCKED`` if any status line says so, else ``DONE``, else ``PARTIAL``,
        else ``RUNNING``. ``BLOCKED`` wins because it is the stricter of the
        terminal states.
    """
    found = {
        match.group(1)
        for match in re.finditer(
            r"^[ \t>*_-]{0,8}STATUS:[ \t]*(BLOCKED|DONE|PARTIAL)\b",
            ledger_text,
            re.MULTILINE,
        )
    }
    for status in ("BLOCKED", "DONE", "PARTIAL"):
        if status in found:
            return status
    return "RUNNING"


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
        Phase number (1, 2, or 3).

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
    if phase not in (1, 2, 3):
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

    is_write, detail = _is_write_attempt(tool, args)
    if not is_write:
        return Verdict(allow=True)

    text = ledger.read_text(encoding="utf-8", errors="replace")
    iterations = count_iterations(text)
    status = read_status(text)

    if status == "DONE":
        return _deny(
            "This bug is already marked STATUS: DONE. Do not edit code. Start a new ledger "
            "for a new bug, or ask the user."
        )
    if status == "BLOCKED":
        return _deny(
            "This bug is marked STATUS: BLOCKED. Do not edit code. Ask the user for the "
            "artefacts you listed and start a new run when they arrive."
        )
    if iterations >= MAX_ITERATIONS:
        return _deny(
            f"Iteration cap reached ({iterations} of {MAX_ITERATIONS}). Do not make another "
            "change. Write STATUS: BLOCKED in the ledger, list every refuted hypothesis, and "
            "ask the user for artefacts."
        )

    phase = read_phase(ledger)
    if phase == 1 and phase_gate_enabled(text) and _is_code_write(detail, ledger, tool):
        return _deny(
            "Phase 1 forbids production-code changes. Write the failing test, the e2e test "
            "and the baseline first; move to Phase 2 with `bugfixer state <ledger> phase 2` "
            "once the root cause is confirmed."
        )

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

    Returns:
        Parsed payload, or an empty dict when stdin is empty or not JSON.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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

    event = _read_event()
    ledger = _resolve_ledger(event, args.ledger)
    verdict = decide(ledger=ledger, tool=_tool_name(event), args=_tool_input(event))
    _emit(verdict)
    return EXIT_ALLOW if verdict.allow else EXIT_DENY


if __name__ == "__main__":
    raise SystemExit(main())
