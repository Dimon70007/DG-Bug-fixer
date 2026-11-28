"""Sub-tasks must not become ledgers (spec §5.3a, invariant 50).

A task file that grows past a thousand lines is read whole by an agent that needs one
sub-section. Splitting it into files is the fix, but the obvious naming —
`tasks/TASK-086.ST-7.md` — leaves the sub-file inside the contract's `tasks/TASK-*.md` glob,
so it becomes a ledger candidate and steals the run by mtime (TASK-086 F9, reproduced).

A directory keeps the sub-task out of the glob. These tests pin both halves: the structure is
usable, and the wrong naming really is dangerous.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
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
    return _load("bugfixer_gate_subtasks", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a contract and a main task file."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    main = tmp_path / "tasks" / "TASK-100.md"
    main.write_text("## Task\nfix the thing\n", encoding="utf-8")
    return tmp_path


def _touch(path: Path, when: float) -> None:
    """Set an exact mtime, so ordering is deterministic."""
    os.utime(path, (when, when))


def test_a_subtask_directory_does_not_steal_the_ledger(gate, project: Path) -> None:
    """The main task stays the ledger even when a sub-task file is newer.

    This is the whole reason the structure is a directory: it keeps the sub-file out of the
    contract's glob.
    """
    folder = project / "tasks" / "TASK-100"
    folder.mkdir()
    sub = folder / "ST-7.md"
    sub.write_text("## Подзадача ST-7\n", encoding="utf-8")

    main = project / "tasks" / "TASK-100.md"
    _touch(main, time.time() - 500)
    _touch(sub, time.time())  # newer, and it must not matter

    assert gate.discover_ledger(project) == main.resolve()


def test_the_dotted_naming_would_steal_the_ledger(gate, project: Path) -> None:
    """The rejected naming: proof that the choice of a directory is not cosmetic.

    `tasks/TASK-100.ST-7.md` matches `tasks/TASK-*.md`, so once it is the newest file the gate
    picks it — the run silently moves to a file that is not a ledger.
    """
    dotted = project / "tasks" / "TASK-100.ST-7.md"
    dotted.write_text("## Подзадача ST-7\n", encoding="utf-8")

    main = project / "tasks" / "TASK-100.md"
    _touch(main, time.time() - 500)
    _touch(dotted, time.time())

    assert gate.discover_ledger(project) == dotted.resolve(), (
        "the dotted name is supposed to be the dangerous one; if it stopped stealing, "
        "the rule in §5.3a can be relaxed"
    )


def test_a_subtask_file_is_not_a_ledger(gate, project: Path) -> None:
    """A sub-file carries no `STATUS:` and no journal: the run state stays in the main file.

    Reading a sub-task's text must not produce a status, because a second status is a second
    source of truth (invariant 34).
    """
    folder = project / "tasks" / "TASK-100"
    folder.mkdir()
    sub = folder / "ST-7.md"
    sub.write_text("## Подзадача ST-7\n\nРешение: каталог, а не точка в имени.\n", encoding="utf-8")

    text = sub.read_text(encoding="utf-8")
    assert gate.read_status(text) == "RUNNING"
    assert not Path(str(sub) + ".bugfixer.jsonl").exists()


def test_the_main_task_keeps_status_and_journal(gate, project: Path) -> None:
    """`STATUS:` and the journal belong to the main file, and only to it."""
    main = project / "tasks" / "TASK-100.md"
    main.write_text("## Task\nfix\n\nSTATUS: DONE\n\n## Report\nИтог: x\n", encoding="utf-8")

    assert gate.read_status(main.read_text(encoding="utf-8")) == "DONE"

    helpers.append(main, event="status", status="DONE")
    assert helpers.journal_path(main).exists()
    assert not (project / "tasks" / "TASK-100").exists(), "no sub-directory was created"
