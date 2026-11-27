"""Cursor-first behaviour, pinned to payloads captured from a live session (Б-3/Б-4, TASK-085).

The gate's other tests check it against *our* idea of a payload. These check it against the shapes
Cursor 3.32.10 actually sent on 2026-11-27, recorded in `tests/fixtures/cursor_payloads.json` — and
three of them were failing before the fixes below, each one a denial that would have hit a real run:

* ``MCP:tavily_search`` was an unknown tool, so phase 3 could not search — the search tool the
  phase exists for was refused on its first call;
* ``pytest -q 2>/dev/null`` was classified as a production write, so phase 1 could not record the
  baseline it exists to record. Silencing stderr is how every suite is run;
* ``CallDynamicTool`` and ``GetDynamicTools`` never reach the hook at all, so a name invented for
  them protects nothing and is recorded here rather than guessed again.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import bugfixer_helpers as jrnl
import pytest

_HOOKS = jrnl.HOOKS
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cursor_payloads.json"

VERIFIED_CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: tavily_search\n"
    "## Extra prohibitions\n\n- no sudo\n"
)


@pytest.fixture()
def gate():
    """The gate module, imported as the plugin ships it."""
    if str(_HOOKS) not in sys.path:
        sys.path.insert(0, str(_HOOKS))
    return importlib.import_module("bugfixer_gate")


def _payloads() -> list[dict[str, Any]]:
    """The captured payload shapes.

    Returns:
        One entry per captured tool, with real key names and nesting.
    """
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _shapes(name: str) -> dict[str, Any]:
    """The captured payload for one tool.

    Args:
        name: ``tool_name`` as Cursor sent it.

    Returns:
        The payload.
    """
    for entry in _payloads():
        if entry["tool_name"] == name:
            return entry
    raise AssertionError(f"{name} was never captured; the fixture is the source of truth")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project in phase 1 with a contract and a ledger."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(VERIFIED_CONTRACT, encoding="utf-8")
    ledger = tmp_path / "tasks" / "TASK-1.md"
    ledger.write_text(
        "## Task\nfix\n\n## Clarifications\nQ: a  A: b\nQ: c  A: d\nQ: e  A: f\n\n"
        "FAILING TEST: pytest -> 1 failed\nBASELINE: 5/5\n",
        encoding="utf-8",
    )
    jrnl.set_phase(ledger, 1)
    return ledger


# --- the payload shapes are what Cursor sends -------------------------------


def test_the_fixture_records_the_shapes_that_were_actually_delivered() -> None:
    """A fixture from imagination is the defect Б-4 describes, so it says where it came from.

    The date and the version are part of the claim: these shapes are a measurement of one Cursor
    release, not a permanent truth. Re-capturing is the documented way to update them.
    """
    shapes = _payloads()
    names = {entry["tool_name"] for entry in shapes}
    assert {"Read", "Shell", "Grep", "Write", "MCP:tavily_search"} <= names
    shell = _shapes("Shell")
    # The measured quirk: `cwd` is an empty string, and the project is in `workspace_roots`.
    assert shell["cwd"] == ""
    assert shell["workspace_roots"]
    assert "cwd" in shell["tool_input"]


def test_a_cursor_payload_yields_the_project_directory(gate) -> None:
    """`cwd` is empty for Shell and absent for Grep, so the gate must read the roots."""
    for name in ("Shell", "Grep", "MCP:tavily_search", "Write"):
        event = dict(_shapes(name))
        event.pop("_shapes", None)
        assert gate._project_root(event) == Path("/project"), name


# --- the search channel phase 3 exists for ----------------------------------


def test_an_mcp_tool_is_not_a_write(gate) -> None:
    """Phase 3 searches through MCP. `MCP:<server>` was unknown, so search was denied."""
    is_write, _ = gate._is_write_attempt("mcp:tavily_search", {"query": "x"})
    assert is_write is False


def test_the_mcp_call_is_allowed_during_a_run(gate, project: Path) -> None:
    """The end-to-end claim: a real search call in phase 1 gets `allow`."""
    event = dict(_shapes("MCP:tavily_search"))
    event.pop("_shapes", None)
    verdict = gate.decide(
        ledger=project, tool=gate._tool_name(event), args=gate._tool_input(event)
    )
    assert verdict.allow, verdict.reason


def test_a_hyphenated_mcp_server_is_covered_by_the_prefix(gate) -> None:
    """Server names are user-chosen, so the rule matches a prefix and never a fixed list."""
    is_write, _ = gate._is_write_attempt("mcp:agent-search", {"query": "x"})
    assert is_write is False


# --- running the suite is not writing a file --------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q 2>/dev/null",
        "python3 -m pytest tests/ 2>/dev/null",
        "pytest -q 1>/dev/null",
        "pytest > /dev/null 2>&1",
        "make test 2>&1 | tail -5",
        "find . -name '*.py' 2>/dev/null",
    ],
)
def test_silencing_a_command_is_not_a_write(gate, command: str) -> None:
    """A redirection into a device writes no file; refusing it blocks the baseline.

    Measured defect: phase 1 cannot run its own suite if `2>/dev/null` counts as a production
    write, and every project silences stderr this way.
    """
    is_write, _ = gate._is_write_attempt("shell", {"command": command})
    assert is_write is False, f"{command} was classified as a write"


def test_the_suite_command_is_allowed_in_phase_one(gate, project: Path) -> None:
    """The end-to-end claim for the baseline step: the usual invocation is not refused."""
    verdict = gate.decide(ledger=project, tool="shell", args={"command": "pytest -q 2>/dev/null"})
    assert verdict.allow, verdict.reason


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > out.txt",
        "cat a.txt > b.txt",
        "rm -rf build 2>/dev/null",
        "pytest -q 2> errors.log",
        "tee build.log",
        "cp src/a.py tests/b.py",
        "mkdir -p logs/dumps",
    ],
)
def test_a_real_write_is_still_a_write(gate, command: str) -> None:
    """The negative control: the exclusion is for devices, not for redirections in general."""
    is_write, _ = gate._is_write_attempt("shell", {"command": command})
    assert is_write is True, f"{command} was not classified as a write"


def test_a_production_write_is_still_refused_in_phase_one(gate, project: Path) -> None:
    """Allowing the suite must not become allowing everything (invariant 13).

    The probe writes to `report.txt`, which §5.5 makes writable in every phase — so the test uses
    a source file, where the phase ban actually applies.
    """
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/report.py"})
    assert not verdict.allow
    assert "Phase 1" in verdict.reason


# --- the honest boundary ----------------------------------------------------


def test_tools_that_never_reach_the_hook_are_recorded_not_invented() -> None:
    """`CallDynamicTool` and `GetDynamicTools` were called and never delivered to `preToolUse`.

    Only the nested `MCP:<server>` call arrived. Adding a name for a tool the hook never sees
    would protect nothing while looking like coverage, so the finding lives in the fixture.
    """
    entry = _shapes("GetDynamicTools")
    assert entry.get("observed") == "not delivered to preToolUse"
    assert "CallDynamicTool" in json.dumps(_payloads())
