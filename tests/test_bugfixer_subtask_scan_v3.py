"""A record moved to a sub-task file must still be checked (spec §5.3a, invariant 26).

Splitting a task into `tasks/TASK-086/ST-9.md` keeps the ledger small — but the secret scan
was implemented as "read the ledger's text and scan every `### F<n>` in it". A record that moved
to a sub-file is not in that text, so it was not checked at all. Measured: the same poisoned
record gave one finding inside the ledger and zero findings once moved.

That is a silent narrowing of invariant 26: the text still looks protected, and the protection
covers only what happens to remain in the main file. The fix widens the scan to the task's own
directory, which is where §5.3a puts sub-tasks.

Every test here failed before the change.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_GATE = helpers.HOOKS / "bugfixer_gate.py"

CONTRACT = """Contract version: 1
Contract verified by: test

## Ledger

Task/bug file: tasks/TASK-*.md
Raw dumps: logs/dumps/

## Tests

Full suite: pytest
Test paths: tests/
"""

#: A token-shaped literal, built so this file does not itself ship one.
POISON = "ghp_" + "a" * 30

RECORD = f"### F1 — probe\n\n**Kind | probe** · **Outcome | CONFIRMED**\n\ntoken: {POISON}\n"


def _load(name: str, path: Path):
    """Import a module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def gate():
    """The gate module under test."""
    return _load("bugfixer_gate_subtask_scan", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a ledger and a sub-task directory."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    ledger = tmp_path / "tasks" / "TASK-100.md"
    ledger.write_text("## Task\nfix the thing\n", encoding="utf-8")
    (tmp_path / "tasks" / "TASK-100").mkdir()
    return ledger


def _sub_dir(ledger: Path) -> Path:
    """The sub-task directory for a ledger, per §5.3a: `TASK-<n>/`, not the whole stem."""
    import re

    match = re.match(r"(TASK-\d+)", ledger.stem)
    return ledger.parent / (match.group(1) if match else ledger.stem)


def test_the_directory_name_is_the_task_number_only(gate, tmp_path: Path) -> None:
    """`tasks/TASK-086-…md` uses `tasks/TASK-086/`, not `tasks/TASK-086-…/`.

    Written because the first implementation used the whole file stem: it looked for
    `TASK-086-bugfixer-context-cost-research/`, found nothing, and reported a clean task while
    three sub-task files sat unread in `TASK-086/`. The unit tests passed because they built
    the directory with the same wrong rule — a test that agrees with the bug proves nothing.
    """
    long_name = tmp_path / "TASK-086-some-long-title.md"
    long_name.write_text("## Task\nx\n", encoding="utf-8")
    assert gate.subtask_directory(long_name) == tmp_path / "TASK-086"

    plain = tmp_path / "TASK-7.md"
    plain.write_text("## Task\nx\n", encoding="utf-8")
    assert gate.subtask_directory(plain) == tmp_path / "TASK-7"


def _problems(gate, ledger: Path) -> list[str]:
    """Every secret problem the gate would report for this run."""
    return gate.secret_problems(ledger, ledger.read_text(encoding="utf-8"))


def test_a_secret_in_the_ledger_is_refused(gate, project: Path) -> None:
    """The baseline behaviour: this one already passed and must keep passing."""
    project.write_text("## Task\nx\n\n" + RECORD, encoding="utf-8")
    assert _problems(gate, project), "a secret in the ledger stopped being refused"


def test_a_secret_in_a_subtask_file_is_refused(gate, project: Path) -> None:
    """The point of the change: a record in a sub-file is still a record.

    Was red — the scan read only the main file.
    """
    sub = _sub_dir(project) / "ST-1.md"
    sub.write_text(RECORD, encoding="utf-8")
    assert _problems(gate, project), (
        "a secret in a sub-task file is not checked; invariant 26 covers only the main file"
    )


def test_the_refusal_names_the_file_the_record_is_in(gate, project: Path) -> None:
    """A refusal that cannot say WHERE cannot be acted on: two sub-files look alike."""
    sub = _sub_dir(project) / "ST-7.md"
    sub.write_text(RECORD, encoding="utf-8")
    problems = _problems(gate, project)
    assert any("ST-7.md" in problem for problem in problems), (
        f"the refusal does not name the file: {problems}"
    )


def test_a_clean_subtask_directory_is_not_refused(gate, project: Path) -> None:
    """The negative control: widening the scan must not refuse ordinary text."""
    sub = _sub_dir(project) / "ST-1.md"
    sub.write_text("### ST-1 — решение\n\nКаталог, а не точка в имени.\n", encoding="utf-8")
    assert _problems(gate, project) == []


def test_a_directory_that_does_not_exist_is_not_an_error(gate, project: Path) -> None:
    """Most tasks have no sub-tasks; the scan must not fail on their absence."""
    assert _sub_dir(project).is_dir()
    _sub_dir(project).rmdir()
    assert _problems(gate, project) == []


def test_nested_subdirectories_are_scanned(gate, project: Path) -> None:
    """A sub-task may itself have a folder; recursion is cheaper than a surprise later."""
    deep = _sub_dir(project) / "ST-9"
    deep.mkdir()
    (deep / "notes.md").write_text(RECORD, encoding="utf-8")
    assert _problems(gate, project), "a record two levels down escaped the scan"
