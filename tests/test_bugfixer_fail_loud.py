"""A broken gate must say so. Silence is indistinguishable from working.

The operator's rule: a plugin error raises, with a recommendation, and never turns itself off
quietly. Spec §0 says the same thing in its own words — "непонятное — это не «нет»" — and the
gate broke that rule in the way that matters most:

    bug-fixer.project.md is present but has no `Task/bug file:`  ->  discover_ledger -> None
                                                                ->  "no run found, gate inactive"
                                                                ->  every production write allowed

That is not "no run is under way"; it is "I was told there is a run and cannot find it". The one
allowed silence is a repository with no contract at all, where no run has been declared and
nothing can be enforced (spec §0's exception: deliberate inertness outside a run).

Each test below names one way the gate used to swallow a problem, and requires a refusal that
says what to do about it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_GATE = jrnl.HOOKS / "bugfixer_gate.py"


def _load_gate():
    """Import the gate module from its file path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("bugfixer_gate_loud", _GATE)
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
    return _load_gate()


def _run(payload: dict, project: Path) -> subprocess.CompletedProcess[str]:
    """Run the gate the way a harness does, from the project directory."""
    return subprocess.run(
        [sys.executable, str(_GATE)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        cwd=str(project),
    )


_WRITE = {"tool_name": "Write", "tool_input": {"file_path": "src/core.py"}}


# --- a declared run with no findable ledger is an error, not "inactive" ------


def test_a_contract_without_a_ledger_field_is_refused_loudly(tmp_path: Path) -> None:
    """The measured defect: a contract that declares a run but names no ledger.

    The gate answered "no run found; gate inactive" and allowed a production write. The
    project has a contract, so a run *is* declared; the plugin simply cannot find its ledger.
    That is a broken install or a broken contract, and it has to say so.
    """
    (tmp_path / "bug-fixer.project.md").write_text(
        "Test paths: tests/\nFull suite: make test\n", encoding="utf-8"
    )
    result = _run(_WRITE, tmp_path)
    assert result.returncode == 2, (
        f"the gate answered {result.returncode} with {result.stdout!r}; a contract that names "
        "no ledger must be refused, not treated as an inactive gate"
    )
    payload = json.loads(result.stdout)
    assert payload["permission"] == "deny"
    message = payload["agent_message"]
    assert "Task/bug file" in message, message
    assert "bugfixer doctor" in message, f"the refusal does not say how to fix it: {message}"


def test_a_contract_whose_pattern_matches_nothing_is_refused_loudly(tmp_path: Path) -> None:
    """A pattern with no file is either a fresh project or a typo; both need a word to a human."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n",
        encoding="utf-8",
    )
    (tmp_path / "tasks").mkdir()
    result = _run(_WRITE, tmp_path)
    assert result.returncode == 2, (
        "with no ledger on disk the gate allowed everything; a project contract declares a run, "
        "so the missing ledger is a problem to report"
    )
    message = json.loads(result.stdout)["agent_message"]
    assert "tasks/TASK-*.md" in message, f"the refusal does not name the pattern: {message}"


def test_two_equally_new_ledgers_are_refused_with_the_candidates_named(tmp_path: Path) -> None:
    """Ambiguity was answered with silence; naming both files is what lets a human choose.

    The timestamps are forced equal with ``os.utime``: two files written one after another on a
    filesystem with sub-second resolution get different mtimes, so writing them in a loop would
    test nothing and pass for the wrong reason.
    """
    import os

    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    paths = []
    for name in ("TASK-1.md", "TASK-2.md"):
        path = tasks / name
        path.write_text("## Task\nx\n", encoding="utf-8")
        paths.append(path)
    stamp = 1_700_000_000
    for path in paths:
        os.utime(path, (stamp, stamp))

    result = _run(_WRITE, tmp_path)
    assert result.returncode == 2
    message = json.loads(result.stdout)["agent_message"]
    assert "TASK-1.md" in message and "TASK-2.md" in message, message
    assert "--ledger" in message, f"the refusal does not offer the way to disambiguate: {message}"


def test_a_missing_ledger_can_still_be_created(tmp_path: Path) -> None:
    """The refusal must not forbid the write that fixes it.

    A contract whose ledger does not exist yet is phase 0. Refusing every write would also
    refuse creating the contract (which `init` does) and the ledger, so the run could never
    start: the gate would be loud and still useless. Setup is the exception; work is not.
    """
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n",
        encoding="utf-8",
    )
    (tmp_path / "tasks").mkdir()
    result = _run(
        {"tool_name": "Write", "tool_input": {"file_path": "tasks/TASK-1.md"}},
        tmp_path,
    )
    assert result.returncode == 0, (
        f"creating the ledger the contract names was refused: {result.stdout}"
    )


def test_creating_the_ledger_does_not_open_production_writes(tmp_path: Path) -> None:
    """The exception is the ledger, not the phase: a code edit is still refused."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n",
        encoding="utf-8",
    )
    (tmp_path / "tasks").mkdir()
    result = _run(_WRITE, tmp_path)
    assert result.returncode == 2


# --- the one allowed silence ------------------------------------------------


def test_no_contract_at_all_is_refused(tmp_path: Path) -> None:
    """Operator decision, 2026-11-27: a run without a contract has no meaning.

    The agent would not know the test command, where findings go, or what the project forbids,
    so the run cannot be correct by construction. A ready default contract beats allowing work
    with none, and a project that wants no plugin removes the hook rather than leaving a gate
    that silently allows everything.
    """
    result = _run(_WRITE, tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    message = json.loads(result.stdout)["agent_message"]
    assert "bugfixer init" in message


def test_absent_and_incomplete_contracts_are_both_refused_with_their_own_advice(
    tmp_path: Path,
) -> None:
    """Both stop the run; the difference is what the operator has to do about it.

    Absent → create one (`bugfixer init`). Present but incomplete → fill in the named field.
    One message for both would send a project that already wrote a contract back to the start.
    """
    absent = _run(_WRITE, tmp_path)
    assert absent.returncode == 2
    assert "bugfixer init" in json.loads(absent.stdout)["agent_message"]

    (tmp_path / "bug-fixer.project.md").write_text("Full suite: make test\n", encoding="utf-8")
    incomplete = _run(_WRITE, tmp_path)
    assert incomplete.returncode == 2
    message = json.loads(incomplete.stdout)["agent_message"]
    assert "Task/bug file" in message, message


# --- unreadable things already deny; keep it that way -----------------------


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("unknown tool", {"tool_name": "frobnicate", "tool_input": {"file_path": "src/x.py"}}),
        ("unreadable shell", {"tool_name": "Bash", "tool_input": {"weird": "touch src"}}),
        ("corrupt payload", None),
    ],
)
def test_unreadable_input_denies(gate, tmp_path: Path, name: str, payload: dict | None) -> None:
    """Already correct, and pinned here so a future refactor cannot soften it into silence."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    ledger = tasks / "TASK-1.md"
    ledger.write_text("## Task\nx\n", encoding="utf-8")

    if payload is None:
        result = subprocess.run(
            [sys.executable, str(_GATE)],
            input="{not json",
            text=True,
            capture_output=True,
            check=False,
            cwd=str(tmp_path),
        )
    else:
        result = _run(payload, tmp_path)
    assert result.returncode == 2, f"{name} was allowed: {result.stdout}"
    assert json.loads(result.stdout)["agent_message"], f"{name} was denied without a reason"


# --- every refusal has to be actionable -------------------------------------


def test_a_refusal_always_carries_a_recommendation(gate, tmp_path: Path) -> None:
    """Spec §0: "упасть с причиной". A reason without a next step is a dead end.

    Checked over a table of refusals rather than one: the failure this prevents is a single
    branch added later that forgets the advice, which no single-case test would catch.
    """
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    ledger = tasks / "TASK-1.md"
    ledger.write_text("## Task\nx\n\nSTATUS: PROBABLY_FINE\n", encoding="utf-8")

    cases = [
        ("production write in phase 1", "write", {"file_path": "src/x.py"}),
        ("unknown status", "write", {"file_path": "tests/a.py"}),
        ("unknown tool", "frobnicate", {"file_path": "src/x.py"}),
    ]
    for name, tool, args in cases:
        verdict = gate.decide(ledger=ledger, tool=tool, args=args)
        assert not verdict.allow, f"{name} was allowed"
        assert len(verdict.reason) > 40, f"{name}: the reason is too terse to act on"
        assert any(
            hint in verdict.reason
            for hint in ("Write", "write", "Run", "Add", "fix", "bugfixer", "STATUS:", "`")
        ), f"{name}: the reason names no next step: {verdict.reason!r}"
