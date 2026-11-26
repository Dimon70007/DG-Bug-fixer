"""Terminal status must name the missing field (TASK-082).

Found by exercising the gate, not by reading it: `BLOCKED` with the required `Причина:` and
`BLOCKED` without it both returned the same refusal. The status check runs first and returns
immediately, so the field checks are unreachable.

Invariants 19, 28 and 29 of `docs/bug-fixer-v3-spec.md` require an *actionable* refusal — it has
to say which field is missing. A message that only says "do not edit code" leaves the agent
unable to tell a forgotten field from a broken gate, which is the failure §0 exists to prevent.

Each test therefore asserts two things: the call is denied, and the reason **names the field**.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_GATE = jrnl.HOOKS / "bugfixer_gate.py"


def _load(name: str, path: Path):
    """Import a module from a file path, registering it for dataclass lookup."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def gate():
    """The gate module."""
    return _load("bugfixer_gate_terminal_fields", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project whose contract allows test writes, so the status is what decides."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    return tmp_path


def _ledger(project: Path, body: str, *, phase: int = 4) -> Path:
    """Write a ledger and put it in a phase.

    Args:
        project: Temp project directory.
        body: Ledger contents.
        phase: Phase for the sidecar.

    Returns:
        The ledger path.
    """
    path = project / "TASK-999.md"
    path.write_text(body, encoding="utf-8")
    (project / "TASK-999.md.bugfixer.json").write_text(
        json.dumps({"phase": phase, "status": "RUNNING", "transitions": []}), encoding="utf-8"
    )
    return path


def _reason(gate, ledger: Path) -> str:
    """Verdict reason for a write into the ledger's project."""
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"})
    assert not verdict.allow, "a terminal status must keep blocking writes"
    return verdict.reason


# --- invariant 29: BLOCKED needs Причина and Логи ----------------------------


def test_blocked_without_a_reason_names_the_reason_field(gate, project: Path) -> None:
    """A forgotten `Причина:` is the agent's mistake to fix, so the refusal says so."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: BLOCKED\nЛоги: logs/run/agent.log\n\n## Report\nИтог: x\n",
    )
    assert "Причина" in _reason(gate, ledger)


def test_blocked_without_logs_names_the_logs_field(gate, project: Path) -> None:
    """Logs are the other half of `BLOCKED`: without them the operator has nothing to act on."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: BLOCKED\nПричина: operator-needed\n\n## Report\nИтог: x\n",
    )
    assert "Логи" in _reason(gate, ledger)


def test_blocked_with_an_unknown_reason_value_is_refused(gate, project: Path) -> None:
    """The §9 list is closed; `Причина: because` is not one of the five values."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: BLOCKED\nПричина: because\nЛоги: logs/x\n\n## Report\nx\n",
    )
    assert "Причина" in _reason(gate, ledger)


def test_well_formed_blocked_still_blocks(gate, project: Path) -> None:
    """The fix must not turn a correct `BLOCKED` into an allow."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: BLOCKED\n"
        "Причина: operator-needed\nЛоги: logs/run/agent.log\n\n## Report\nИтог: x\n",
    )
    reason = _reason(gate, ledger)
    assert "BLOCKED" in reason


# --- invariant 28: PARTIAL needs ## Remaining --------------------------------


def test_partial_without_remaining_names_the_section(gate, project: Path) -> None:
    """`PARTIAL` is a handover; without `## Remaining` there is nothing handed over."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: PARTIAL\n\n## Report\nИтог: x\n",
        phase=5,
    )
    assert "Remaining" in _reason(gate, ledger)


def test_partial_with_an_empty_remaining_names_the_section(gate, project: Path) -> None:
    """A heading with no items means the work is DONE, not partial."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: PARTIAL\n\n## Remaining\n\n## Report\nИтог: x\n",
        phase=5,
    )
    assert "Remaining" in _reason(gate, ledger)


# --- invariant 28: the section's own form, taken from the spec ---------------


def test_remaining_heading_copied_from_the_spec_is_accepted(gate, project: Path) -> None:
    """§4 of the spec writes the marker as `## Remaining: <список>`.

    An agent that transcribes the spec literally must not be refused for it. The
    placeholder belongs to the marker table, so the heading may carry it; what matters
    is that the section exists and holds the handover.
    """
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: PARTIAL\n\n## Remaining: <список>\n- left: x\n\n"
        "## Report\nИтог: x\n",
        phase=5,
    )
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"})
    assert "Remaining" not in verdict.reason, verdict.reason


def test_report_heading_copied_from_the_spec_is_accepted(gate, project: Path) -> None:
    """§4 writes it as `## Report (поля §7)`; the parenthetical is not part of the name."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: PARTIAL\n\n## Remaining\n- left: x\n\n"
        "## Report (поля §7)\nИтог: x\n",
        phase=5,
    )
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"})
    assert "Report" not in verdict.reason, verdict.reason


def test_remaining_as_a_table_is_accepted(gate, project: Path) -> None:
    """The handover is a table in this repository's own `PARTIAL` ledger (TASK-079.2).

    §9 names four fields per item — what, why not done, how verified, who closes it —
    which is exactly a table. A bullet-only check refuses the form the project writes.
    """
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: PARTIAL\n\n## Remaining\n\n"
        "| What | Why not done | How verified | Owner |\n"
        "|---|---|---|---|\n"
        "| wire Codex | `codex` not on PATH | same matrix | operator |\n\n"
        "## Report\nИтог: x\n",
        phase=5,
    )
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"})
    assert "Remaining" not in verdict.reason, verdict.reason


def test_remaining_as_a_numbered_list_is_accepted(gate, project: Path) -> None:
    """`1.` is a list too; refusing it teaches the agent a spelling, not a discipline."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\nSTATUS: PARTIAL\n\n## Remaining\n\n"
        "1. wire Codex — `codex` not on PATH — same matrix — operator\n\n"
        "## Report\nИтог: x\n",
        phase=5,
    )
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"})
    assert "Remaining" not in verdict.reason, verdict.reason


# --- invariant 19: a status is written with its report ----------------------


def test_status_without_a_report_names_the_report(gate, project: Path) -> None:
    """A status with no `## Report` is a decision nobody can audit."""
    ledger = _ledger(
        project,
        "## Task\nfix it\n\n## Remaining\n- item one\n\nSTATUS: PARTIAL\n",
        phase=5,
    )
    assert "Report" in _reason(gate, ledger)
