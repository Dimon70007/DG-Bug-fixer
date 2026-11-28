"""A secret must be removable, and not introducible (F19, spec §10).

Measured defect: the scan read the ledger **from disk**, never the body a write would produce. So a
`StrReplace` deleting the offending line was refused, because the scan saw the line it was about to
remove — and the refusal messages said "Remove it", asking for the action they blocked. There was no
way out but a human editing the file (TASK-086 F19).

The fix judges the prospective body. That is also the shape of a bypass, so the tests come in pairs:
the repair must pass, and every way of smuggling a secret in must not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_GATE = helpers.HOOKS / "bugfixer_gate.py"

CONTRACT = (
    "Contract version: 1\nContract verified by: agent 2026-01-01 — test\n\n"
    "## Ledger\n\nTask/bug file: TASK-*.md\nRaw dumps: logs/dumps/\n\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
)

CLEAN = (
    "## Task\nfix the thing\n\n## Clarifications\n"
    "Q: a  A: b\nQ: c  A: d\nQ: e  A: f\n"
    "\nFAILING TEST: pytest tests/x.py -> 1 failed\nBASELINE: 10/10 green\n"
    "\n### F1 — probe\n| Field | Value |\n|---|---|\n| Note | clean |\n"
)

#: Fictional value. §10 forbids quoting a real secret's value, length or prefix — the shape of an
#: assignment is all the scanner needs.
SECRET_LINE = "| Note | password = EXAMPLE-not-a-real-value |"
POISONED = CLEAN.replace("| Note | clean |", SECRET_LINE)


def _load(name: str, path: Path):
    """Import a module from a file path.

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
    return _load("bugfixer_gate_secret_prospective", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A phase-1 project with a clean ledger.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        Path of the ledger.
    """
    (tmp_path / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    for name in ("src", "tests", "logs/dumps"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    ledger = tmp_path / "TASK-999.md"
    ledger.write_text(CLEAN, encoding="utf-8")
    return ledger


def _decide(gate, ledger: Path, tool: str, args: dict):
    """Run the gate's decision path.

    Args:
        gate: The gate module.
        ledger: Ledger to enforce.
        tool: Lowercased tool name.
        args: Tool arguments.

    Returns:
        The verdict.
    """
    return gate.decide(ledger=ledger, tool=tool, args=args, dev_plugin=True)


# --- the defect: the repair was impossible -----------------------------------


def test_a_write_that_removes_the_secret_is_allowed(gate, project: Path) -> None:
    """The one action the refusal message asks for must be the one action that works."""
    project.write_text(POISONED, encoding="utf-8")
    verdict = _decide(gate, project, "write", {"file_path": str(project), "content": CLEAN})
    assert verdict.allow, verdict.reason


def test_a_replace_that_removes_the_secret_is_allowed(gate, project: Path) -> None:
    """`StrReplace` deletes a line; the scan must judge the file it produces, not the one it has."""
    project.write_text(POISONED, encoding="utf-8")
    verdict = _decide(
        gate,
        project,
        "strreplace",
        {"file_path": str(project), "old_string": SECRET_LINE, "new_string": "| Note | removed |"},
    )
    assert verdict.allow, verdict.reason


def test_a_clean_write_to_a_clean_ledger_is_still_allowed(gate, project: Path) -> None:
    """The control: the fix must not turn every ledger write into a refusal."""
    verdict = _decide(gate, project, "write", {"file_path": str(project), "content": CLEAN})
    assert verdict.allow, verdict.reason


# --- the bypass: every way in must stay refused ------------------------------


def test_a_write_that_introduces_a_secret_is_refused(gate, project: Path) -> None:
    """Judging the prospective body must not become "trust the payload"."""
    verdict = _decide(gate, project, "write", {"file_path": str(project), "content": POISONED})
    assert not verdict.allow
    assert verdict.verdict_of == "secret-in-record"


def test_a_replace_that_introduces_a_secret_is_refused(gate, project: Path) -> None:
    """This was a real bypass while the fix was being written.

    A fragment scanned on its own says nothing about the text around it, so `new_string` carrying
    a secret was allowed. Caught by `probes/f19_bypass_check.py`, not by reading the code.
    """
    verdict = _decide(
        gate,
        project,
        "strreplace",
        {"file_path": str(project), "old_string": "| Note | clean |", "new_string": SECRET_LINE},
    )
    assert not verdict.allow
    assert verdict.verdict_of == "secret-in-record"


def test_an_unreconstructable_edit_falls_back_to_the_file(gate, project: Path) -> None:
    """When the payload cannot be applied, the file on disk decides — stricter, never looser."""
    project.write_text(POISONED, encoding="utf-8")
    verdict = _decide(
        gate,
        project,
        "strreplace",
        {"file_path": str(project), "old_string": "text that is not there", "new_string": "x"},
    )
    assert not verdict.allow
    assert verdict.verdict_of == "secret-in-record"


def test_a_secret_in_an_untouched_subtask_file_does_not_block(gate, project: Path) -> None:
    """Access is not writing: a stale record elsewhere must not stop a clean write.

    The earlier version of this test asserted the opposite, and that was the defect: measured
    (`probes/secret_presence_vs_write.py`), one poisoned sub-task file refused writes to the
    ledger, to logs and to documentation — none of which writes a secret. Operator's ruling
    2026-11-28 15:02: invariant 26 forbids writing a secret, not working near one.
    """
    folder = project.parent / "TASK-999"
    folder.mkdir()
    (folder / "ST-1.md").write_text(f"# ST\n{SECRET_LINE}\n", encoding="utf-8")
    verdict = _decide(gate, project, "write", {"file_path": str(project), "content": CLEAN})
    assert verdict.allow, verdict.reason


def test_a_lingering_secret_refuses_the_finish(gate, project: Path) -> None:
    """The run cannot be declared finished while a secret sits in its files.

    This is where the ruling puts the pressure: writing stays open so work can continue, and the
    finish stays shut so an unresolved leak cannot be recorded as a completed change.
    """
    folder = project.parent / "TASK-999"
    folder.mkdir()
    (folder / "ST-1.md").write_text(f"# ST\n{SECRET_LINE}\n", encoding="utf-8")
    problems = gate.secret_problems(project, project.read_text(encoding="utf-8"))
    assert problems, "the CLI refuses a terminal status on exactly this list"
    assert "ST-1.md" in problems[0]


def test_the_fix_to_a_subtask_file_is_allowed(gate, project: Path) -> None:
    """The same repair must work in a sub-task file, which is where records live after ST-7."""
    folder = project.parent / "TASK-999"
    folder.mkdir()
    sub = folder / "ST-1.md"
    sub.write_text(f"# ST\n{SECRET_LINE}\n", encoding="utf-8")
    verdict = _decide(
        gate, project, "write", {"file_path": str(sub), "content": "# ST\n| Note | clean |\n"}
    )
    assert verdict.allow, verdict.reason


# --- the norm must be editable by the rule it defines ---------------------------


def _spec_like(tmp_path: Path) -> Path:
    """A document that quotes forbidden forms in order to forbid them (§10's own table).

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        Path of the document.
    """
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    document = docs / "spec.md"
    document.write_text(
        "# Norm\n\n| Forbidden | Allowed |\n|---|---|\n" + SECRET_LINE + "\n\nProse to correct.\n",
        encoding="utf-8",
    )
    return document


def test_a_document_that_quotes_forbidden_forms_is_editable(gate, project: Path) -> None:
    """A rule has to quote what it forbids; scanning any addressed file made it uneditable.

    Measured (`probes/st12_norm_shapes.py`, TASK-086 ST-12): every `StrReplace` on
    `docs/bug-fixer-v3-spec.md` was refused with `secret in … at line 1007` — the §10 table — no
    matter what the edit changed, including the edit that corrected the norm. Invariant 26 is about
    records, not about every document in the project.
    """
    document = _spec_like(project.parent)
    verdict = _decide(
        gate,
        project,
        "write",
        {"file_path": str(document), "content": document.read_text(encoding="utf-8") + "more\n"},
    )
    assert verdict.allow, verdict.reason


def test_a_new_record_file_is_still_scanned(gate, project: Path) -> None:
    """The bound must not reopen the hole it was written around: a new record is a record.

    Before, the file did not exist, `subtask_files` listed what was on disk, and a `Write` of a
    poisoned new `TASK-<n>/ST-1.md` was allowed while the same write to an existing file was
    refused.
    """
    folder = project.parent / "TASK-999"
    folder.mkdir(exist_ok=True)
    verdict = _decide(
        gate, project, "write", {"file_path": str(folder / "ST-2.md"), "content": SECRET_LINE}
    )
    assert not verdict.allow
    assert verdict.verdict_of == "secret-in-record"


def test_a_markdown_beside_the_ledger_is_a_record(gate, project: Path) -> None:
    """Findings and notes sit beside the ledger (§5.3a), so they are records too."""
    verdict = _decide(
        gate,
        project,
        "write",
        {"file_path": str(project.parent / "TASK-999-notes.md"), "content": SECRET_LINE},
    )
    assert not verdict.allow
    assert verdict.verdict_of == "secret-in-record"


def test_the_ledger_itself_is_a_record(gate, project: Path) -> None:
    """The task file is where findings are written, and it stays scanned.

    The ledger needs no branch in the bound — it is always among the scanned bodies — but the rule
    it protects is that a record is refused, so the case is pinned here rather than assumed from
    the shape of the code.
    """
    verdict = _decide(gate, project, "write", {"file_path": str(project), "content": POISONED})
    assert not verdict.allow
    assert verdict.verdict_of == "secret-in-record"
