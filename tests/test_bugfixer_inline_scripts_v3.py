"""Inline scripts are refused by form, and the way out is a file (spec §5.6, invariant 51).

Measured motive (TASK-086 ST-11): across 12 161 real `Shell` calls in 125 transcripts, 1 683
were inline scripts — 1 044 heredocs into an interpreter and 614 `-c` arguments. Of those, 584
only read, so the gate passed them: the ban was partial, and the partial form is what let the
habit survive. A ban is also only honest if it names what to do instead, so the refusal text is
tested as carefully as the verdict.

Every test here fails against the code before ST-11 — or, where it protects existing behaviour
(`pytest -q` still allowed, `src/**` still refused), it fails if the change over-reaches.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_GATE = helpers.HOOKS / "bugfixer_gate.py"

BASELINE = (
    "## Task\nfix the thing\n\n## Clarifications\n"
    "Q: a  A: b\nQ: c  A: d\nQ: e  A: f\n"
    "\nFAILING TEST: pytest tests/x.py -> 1 failed\nBASELINE: 10/10 green\n"
)


def _load(name: str, path: Path):
    """Import a module from a file path, registering it for dataclass lookup.

    Args:
        name: Module name to register.
        path: File to import.

    Returns:
        The loaded module.
    """
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
def gate():
    """The gate module under test."""
    return _load("bugfixer_gate_inline", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a contract, a ledger, a source tree and a shipped script.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        Path of the ledger.
    """
    (tmp_path / "bug-fixer.project.md").write_text(
        "Contract version: 1\nContract verified by: test\n\n"
        "## Ledger\n\nTask/bug file: TASK-*.md\nRaw dumps: logs/dumps/\n\n"
        "## Tests\n\nFull suite: pytest\nTest paths: tests/\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "logs" / "dumps").mkdir(parents=True)
    (tmp_path / "scripts" / "linux").mkdir(parents=True)
    (tmp_path / "probes").mkdir()
    (tmp_path / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
    # A shipped tool: editing it changes behaviour, which is what the phase ban protects.
    (tmp_path / "scripts" / "linux" / "shipped.py").write_text("print('shipped')\n", encoding="utf-8")
    ledger = tmp_path / "TASK-999.md"
    ledger.write_text(BASELINE, encoding="utf-8")
    return ledger


def _denials(ledger: Path) -> list[dict]:
    """Denial records from the ledger's journal.

    Args:
        ledger: Task file.

    Returns:
        Records whose ``event`` is ``deny``.
    """
    path = helpers.journal_path(ledger)
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == "deny"
    ]


# --- the ban is on the form, not on the content ------------------------------


def test_a_read_only_dash_c_is_refused(gate, project: Path) -> None:
    """`python3 -c "print(1)"` writes nothing, and is still refused.

    The gate cannot read the program, so it cannot tell this from `open('f','w')`. Refusing the
    form is the only answer that is not a guess — and 584 such commands really pass today.
    """
    verdict = gate.decide(ledger=project, tool="shell", args={"command": 'python3 -c "print(1)"'})
    assert not verdict.allow
    assert verdict.verdict_of == "inline-script"


def test_a_read_only_heredoc_is_refused(gate, project: Path) -> None:
    """A heredoc whose body only reads is refused by the same rule.

    `cat <<EOF` is data and stays allowed (invariant 45a); the moment the body is fed to an
    interpreter it is a program, and the gate is back to not reading it.
    """
    verdict = gate.decide(
        ledger=project, tool="shell", args={"command": "python3 - <<'PY'\nprint(1)\nPY"}
    )
    assert not verdict.allow
    assert verdict.verdict_of == "inline-script"


def test_the_refusal_names_the_way_out(gate, project: Path) -> None:
    """A ban with no exit is a wall: the message must name the file, the linter and the order.

    Checked as text because that is what the agent reads. A refusal that only says "not allowed"
    sends it back to the same heredoc with a different quoting trick.
    """
    verdict = gate.decide(ledger=project, tool="shell", args={"command": 'python3 -c "print(1)"'})
    reason = verdict.reason
    assert "scripts/" in reason, "the message must name where the script goes"
    assert "py_compile" in reason or "ruff" in reason, "the message must name the lint step"
    assert "run it by name" in reason or "scripts/<name>.py" in reason
    # The two linters that may not exist on a machine must be paired with a working default.
    assert "bash -n" in reason or "shellcheck" in reason


def test_a_wrapped_interpreter_is_refused_too(gate, project: Path) -> None:
    """`poetry run python -c …` is the same inline script behind a wrapper.

    Found by measurement, not by reading the code: 26 real commands in this repository spell it
    this way, and the first version of the rule missed every one — the interpreter sat after
    `poetry run`, so the pattern that looks for a segment-initial interpreter never fired.
    A test, not the probe, has to keep this closed.
    """
    for command in (
        'poetry run python -c "print(1)"',
        'env python3 -c "print(1)"',
        'cd /repo && poetry run python -c "print(1)"',
        'sudo -E python3 -c "print(1)"',
    ):
        verdict = gate.decide(ledger=project, tool="shell", args={"command": command})
        assert not verdict.allow, f"{command} was allowed"
        assert verdict.verdict_of == "inline-script", f"{command}: {verdict.verdict_of}"


def test_a_wrapped_script_file_is_still_allowed(gate, project: Path) -> None:
    """The wrapper check must not swallow the permitted form: `poetry run python script.py`."""
    verdict = gate.decide(
        ledger=project, tool="shell", args={"command": "poetry run python scripts/new.py"}
    )
    assert verdict.allow, verdict.reason


def test_an_ordinary_command_is_still_allowed(gate, project: Path) -> None:
    """The check must not swallow normal calls: `pytest -q` has no interpreter and no program."""
    assert gate.decide(ledger=project, tool="shell", args={"command": "pytest -q"}).allow
    assert gate.decide(ledger=project, tool="shell", args={"command": "rg pattern src/"}).allow


def test_running_a_script_file_is_allowed(gate, project: Path) -> None:
    """`python3 scripts/x.py` is the required form, so it is never the inline rule's business."""
    verdict = gate.decide(
        ledger=project, tool="shell", args={"command": "python3 scripts/linux/new.py"}
    )
    assert verdict.allow, verdict.reason


def test_a_quote_inside_a_program_is_still_the_same_refusal(gate, project: Path) -> None:
    """The old defect was a false refusal *because of* a `>` inside quotes.

    The point of moving to a form-based ban is that the verdict no longer depends on what the
    program contains: `a > b` in quotes and `open('f','w')` get the same answer, and it is the
    same answer as a program with neither.
    """
    with_arrow = gate.decide(
        ledger=project, tool="shell", args={"command": "python3 -c \"print('a > b')\""}
    )
    with_write = gate.decide(
        ledger=project, tool="shell", args={"command": "python3 -c \"open('f','w')\""}
    )
    assert with_arrow.verdict_of == with_write.verdict_of == "inline-script"


# --- the exit: a new file is not a product change ----------------------------


def test_a_new_script_may_be_written_in_phase_one(gate, project: Path) -> None:
    """The ban is only honest if the alternative works in the phase that collects evidence.

    Measured before the change: `scripts/` was refused in phase 1, which left `logs/` as the
    only writable place for a script — the F13 hole, used as a workaround.
    """
    verdict = gate.decide(
        ledger=project, tool="write", args={"file_path": "scripts/linux/new_probe.py"}
    )
    assert verdict.allow, verdict.reason


def test_a_new_probe_may_be_written_in_phase_one(gate, project: Path) -> None:
    """`probes/` is the documented home of a one-off measurement, so it is allowed too."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "probes/new.py"})
    assert verdict.allow, verdict.reason


def test_editing_a_shipped_script_is_still_a_code_change(gate, project: Path) -> None:
    """The boundary that keeps the exit from becoming a bypass.

    In this repository `scripts/linux/*.py` is tracked production code. "Allow scripts/**" without
    the existence test would let phase 1 rewrite a shipped tool with no hypothesis — the hole the
    phase ban exists to close.
    """
    verdict = gate.decide(
        ledger=project, tool="write", args={"file_path": "scripts/linux/shipped.py"}
    )
    assert not verdict.allow
    assert verdict.verdict_of == "phase-ban"


def test_a_source_edit_is_still_refused(gate, project: Path) -> None:
    """The exit is about new scripts; `src/**` keeps the phase rule untouched."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    assert not verdict.allow
    assert verdict.verdict_of == "phase-ban"


def test_a_new_script_outside_the_project_is_refused(gate, project: Path) -> None:
    """Invariant 43 outranks the exit: a new script still has to be inside the project."""
    verdict = gate.decide(
        ledger=project, tool="write", args={"file_path": "/outside-a/scripts/new.py"}
    )
    assert not verdict.allow
    assert verdict.verdict_of == "outside-project"


def test_a_new_script_is_not_a_ledger_write(gate, project: Path) -> None:
    """A control: the exit must not make every path writable, only the two script trees.

    `tools/` is deliberately not one of them — the directories are named, not inferred from
    "sounds like a script".
    """
    (project.parent / "tools").mkdir(exist_ok=True)
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "tools/new.py"})
    assert not verdict.allow
    assert verdict.verdict_of == "phase-ban"
