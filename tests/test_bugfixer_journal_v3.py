"""The run journal, spec §5.4 and invariants 31–36 (TASK-085 Ж-1…Ж-3).

The journal exists to show how a run reached its current state, so the properties under
test are all about *who may write it* and *what it is allowed to claim*:

* append-only — a transition grows the file by one line and rewrites nothing;
* written by the runtime, never the agent — a write through a tool is refused with a
  reason naming the owner;
* the single source of truth for the phase — the old sidecar no longer moves anything,
  so the forgery that used to open the Phase-1 ban has nothing to forge;
* never a precondition — no journal means phase 1, not a refusal.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_SKILL = jrnl.SKILL_DIR
_HOOKS = jrnl.HOOKS
_GATE = _HOOKS / "bugfixer_gate.py"
_STATE = _HOOKS / "bugfixer_gate_state.py"
_JOURNAL = _HOOKS / "bugfixer_journal.py"

BASELINE = (
    "## Task\nfix the thing\n\n## Clarifications\n"
    "Q: a  A: b\nQ: c  A: d\nQ: e  A: f\n"
    "\nFAILING TEST: pytest tests/x.py -> 1 failed\nBASELINE: 10/10 green\n"
)


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


@pytest.fixture()
def journal():
    """The journal module."""
    return _load("bugfixer_journal_test", _JOURNAL)


@pytest.fixture()
def gate():
    """The gate module."""
    return _load("bugfixer_gate_journal_test", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A minimal project: a contract, a ledger with a reproduction, no journal."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "TASK-999.md"
    ledger.write_text(BASELINE, encoding="utf-8")
    return ledger


def _sidecar(ledger: Path) -> Path:
    """Path of the 2.x state file, which must no longer mean anything."""
    return ledger.parent / (ledger.name + ".bugfixer.json")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the state CLI."""
    return subprocess.run(
        [sys.executable, str(_STATE), *args], text=True, capture_output=True, check=False
    )


# --- §5.4: the file, its place, its format -----------------------------------


def test_the_journal_sits_beside_the_task_file(journal, project: Path) -> None:
    """§5.4: one journal per task, named after the task file."""
    assert journal.journal_path(project) == project.parent / "TASK-999.md.bugfixer.jsonl"


def test_an_event_is_one_json_line_with_the_required_fields(journal, project: Path) -> None:
    """§5.4's field table: ``at``, ``event`` and ``why`` on every record; one per line."""
    journal.append_event(project, event="start", why="phase 0 done")
    lines = journal.journal_path(project).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "start"
    assert record["why"] == "phase 0 done"
    assert record["at"].endswith("Z")


def test_a_record_cannot_carry_a_newline_in_its_reason(journal, project: Path) -> None:
    """One line is one event; a reason with a break would split it into two."""
    journal.append_event(project, event="deny", why="line one\nline two")
    lines = journal.journal_path(project).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


# --- invariant 32: append only -----------------------------------------------


def test_an_advance_grows_the_journal_by_exactly_one_line(project: Path) -> None:
    """Invariant 32: the previous records are untouched, one new line is added.

    The second move is ``ALREADY_TRIED`` (2→3), which needs a paid attempt — the fixture
    carries one so the transition is legal and the growth is what the test measures.
    """
    project.write_text(BASELINE + "\nAttempt 1 consumed\n", encoding="utf-8")
    first_move = _run(str(project), "advance", "2", "--evidence", "RED + baseline")
    assert first_move.returncode == 0, first_move.stderr
    path = project.parent / "TASK-999.md.bugfixer.jsonl"
    first = path.read_text(encoding="utf-8").splitlines()
    second_move = _run(str(project), "advance", "3", "--evidence", "attempt paid, nothing confirmed")
    assert second_move.returncode == 0, second_move.stderr
    second = path.read_text(encoding="utf-8").splitlines()
    assert len(second) == len(first) + 1
    assert second[: len(first)] == first


def test_a_refused_transition_still_records_nothing_new_about_the_phase(project: Path) -> None:
    """A refusal is history, or it never happened; it must not advance the run either."""
    before = _run(str(project), "advance", "4", "--evidence", "skip ahead")
    assert before.returncode == 2
    state = json.loads(_run(str(project)).stdout)
    assert state["phase"] == 1


# --- invariant 34: the last record is the state ------------------------------


def test_the_phase_comes_from_the_last_advance(project: Path) -> None:
    """Invariant 34: no second file — the newest ``advance`` says where the run is."""
    _run(str(project), "advance", "2", "--evidence", "RED + baseline")
    path = project.parent / "TASK-999.md.bugfixer.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"at": "2026-01-01T00:00:00Z", "event": "advance", "from": 2, "to": 3,
                        "edge": "ALREADY_TRIED", "why": "attempt paid"})
            + "\n"
        )
    assert json.loads(_run(str(project)).stdout)["phase"] == 3


def test_a_status_event_is_visible_in_the_state(project: Path) -> None:
    """``status`` is one of the six event kinds; the reader must not lose it."""
    assert _run(str(project), "status", "PARTIAL").returncode == 0
    assert json.loads(_run(str(project)).stdout)["status"] == "PARTIAL"


def test_a_corrupt_journal_line_fails_closed(project: Path) -> None:
    """An unreadable record is not "probably phase 1": the gate must say so loudly."""
    (project.parent / "TASK-999.md.bugfixer.jsonl").write_text("{not json\n", encoding="utf-8")
    result = _run(str(project))
    assert result.returncode == 2
    assert "TASK-999.md.bugfixer.jsonl" in result.stderr


# --- invariant 33: the runtime writes it, the agent does not -----------------


@pytest.mark.parametrize("tool", ["write", "strreplace"])
def test_an_agent_write_to_the_journal_is_denied(gate, project: Path, tool: str) -> None:
    """Invariant 33: a journal the agent can edit is a phase the agent can choose."""
    verdict = gate.decide(
        ledger=project, tool=tool, args={"file_path": str(project) + ".bugfixer.jsonl"}
    )
    assert not verdict.allow, "the agent was allowed to write the journal"
    assert "journal" in verdict.reason.lower(), verdict.reason
    assert "Phase" not in verdict.reason, "refused as a code write, not as the runtime's file"


def test_an_agent_delete_of_the_journal_is_denied(gate, project: Path) -> None:
    """Erasing the file erases the history the refusal is meant to protect."""
    verdict = gate.decide(
        ledger=project, tool="bash", args={"command": "rm TASK-999.md.bugfixer.jsonl"}
    )
    assert not verdict.allow, "the agent was allowed to delete the journal"
    assert "journal" in verdict.reason.lower(), verdict.reason


def test_the_refusal_names_the_owner(gate, project: Path) -> None:
    """The agent has to learn who writes it, or it will try again with another spelling."""
    verdict = gate.decide(
        ledger=project, tool="write", args={"file_path": "TASK-999.md.bugfixer.jsonl"}
    )
    assert "gate" in verdict.reason.lower() or "runtime" in verdict.reason.lower(), verdict.reason


# --- §5.4's event kinds: refusals are history too ----------------------------


def test_a_refused_write_is_recorded_with_the_tool_and_the_reason(gate, project: Path) -> None:
    """§5.4 lists ``deny`` among the events; a refusal nobody wrote down never happened.

    The record names the tool and the reason, so the journal answers "why was I blocked
    here" without replaying the run.
    """
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/x.py"})
    assert not verdict.allow
    denials = [r for r in gate.journal.read_events(project) if r["event"] == "deny"]
    assert denials, "a refusal left no record"
    assert denials[0]["tool"] == "write"
    assert "Phase 1" in denials[0]["why"]


def test_a_denial_record_does_not_move_the_phase(gate, project: Path) -> None:
    """Recording a refusal is an observation; the state must still read phase 1."""
    gate.decide(ledger=project, tool="write", args={"file_path": "src/x.py"})
    assert json.loads(_run(str(project)).stdout)["phase"] == 1


def test_an_allowed_write_records_no_denial(gate, project: Path) -> None:
    """Only refusals are denials; a clean allow must not fill the journal."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "tests/a.py"})
    assert verdict.allow, verdict.reason
    assert not [r for r in gate.journal.read_events(project) if r["event"] == "deny"]


# --- invariant 36: absence is not a refusal ----------------------------------


def test_without_a_journal_the_run_is_in_phase_1(project: Path) -> None:
    """Invariant 36: the journal observes the run; it does not gate it."""
    assert json.loads(_run(str(project)).stdout)["phase"] == 1


def test_without_a_journal_the_task_file_is_still_writable(gate, project: Path) -> None:
    """Invariant 36 with 31: no journal must not close the ledger."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": str(project)})
    assert verdict.allow, verdict.reason


# --- the forgery this closes ------------------------------------------------


def test_the_old_sidecar_no_longer_moves_the_phase(gate, project: Path) -> None:
    """The reproduced forgery: writing ``{"phase": 4}`` used to open the Phase-1 ban.

    With the phase read from an append-only journal the agent cannot write, the number
    has to be produced by the state CLI, which checks the edge and its guard.
    """
    _sidecar(project).write_text(json.dumps({"phase": 4, "status": "RUNNING"}), encoding="utf-8")
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/x.py"})
    assert not verdict.allow, "an agent-written state file still chose the phase"
    assert "Phase 1" in verdict.reason


def test_the_gate_reads_the_phase_the_cli_recorded(gate, project: Path) -> None:
    """The legal path stays open: a proved edge moves the run and the gate follows."""
    assert _run(str(project), "advance", "2", "--evidence", "RED + baseline").returncode == 0
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/x.py"})
    assert not verdict.allow
    assert "Phase 2" in verdict.reason


# --- invariant 35: never in git ---------------------------------------------


def test_the_journal_is_ignored_in_the_plugin_tree() -> None:
    """Invariant 35: history of a run is local, so the extension must be ignored.

    Checked by reading the plugin's own ``.gitignore`` rather than by asking git: the
    suite has to pass in a clone (where git works) and in an unpacked copy (where there is
    no repository at all), and a rule that only holds inside a checkout is not a rule the
    plugin carries.
    """
    rules = (_SKILL / ".gitignore").read_text(encoding="utf-8").splitlines()
    patterns = {line.strip() for line in rules if line.strip() and not line.startswith("#")}
    for name in ("TASK-999.md.bugfixer.jsonl", "TASK-999.md.bugfixer.json"):
        assert any(
            fnmatch.fnmatch(name, pattern) for pattern in patterns
        ), f"{name} is not ignored by {_SKILL / '.gitignore'}: {sorted(patterns)}"

