"""A refusal must be explainable from the journal alone (spec §5.4, invariant 36).

Before this module the record carried `at`, `event`, `why` and `tool`: it said *which tool*
was refused, never *what* the tool was pointed at, in *which phase*, under *which rule*, and
in *whose journal*. Measured consequence (TASK-086 F5/F10/F24): 111 of 133 refusals in two
real journals cannot be adjudicated at all, so no decision about tightening or loosening a
rule can be based on them.

Every test here is written to FAIL against the code before TASK-087. The ones that pass on
the old code are noted as such, because a test that cannot fail proves only that green
exists.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_SKILL = helpers.SKILL_DIR
_GATE = helpers.HOOKS / "bugfixer_gate.py"
_JOURNAL = helpers.HOOKS / "bugfixer_journal.py"

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
    return _load("bugfixer_gate_deny_fields", _GATE)


@pytest.fixture()
def journal():
    """The journal module under test."""
    return _load("bugfixer_journal_deny_fields", _JOURNAL)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a contract, a ledger and a source tree.

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
    (tmp_path / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
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


# --- target: what the tool was pointed at ------------------------------------


def test_a_shell_denial_records_the_command(gate, project: Path) -> None:
    """`tool: shell` alone cannot distinguish a heredoc into the ledger from a source edit.

    This is the field F10 names as the one that makes 51 shell refusals unreadable.
    """
    command = "cat >> src/service.py <<'EOF'\ny = 2\nEOF"
    verdict = gate.decide(ledger=project, tool="shell", args={"command": command})
    assert not verdict.allow
    record = _denials(project)[0]
    assert record.get("target") == command, f"target missing or altered: {record.get('target')!r}"


def test_a_write_denial_records_the_path(gate, project: Path) -> None:
    """A write tool carries its destination in the payload; the record must keep it."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    assert not verdict.allow
    record = _denials(project)[0]
    assert record.get("target") == "src/service.py"


def test_a_multiline_command_stays_one_record(gate, project: Path) -> None:
    """A heredoc has newlines; the journal is JSON Lines, so the record must not split.

    The `why` field is folded for the same reason. `target` keeps its newlines because JSON
    escapes them, and the round-trip has to be exact or the command cannot be replayed.
    """
    command = "cat > src/service.py <<'EOF'\nline one\nline two\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    path = helpers.journal_path(project)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1, "a record was split by the newlines inside the command"
    assert json.loads(lines[0])["target"] == command


def test_a_long_command_is_capped_and_marked(gate, project: Path) -> None:
    """An 8 KB heredoc would become an 8 KB journal record; the field is capped and says so.

    Measured on a real command (TASK-086): a heredoc carrying a whole ledger produced an
    8 019-character field. The cap is 400 characters, with a flag so a reader knows the
    command was truncated rather than genuinely short.
    """
    command = "cat > src/service.py <<'EOF'\n" + ("line of text\n" * 500) + "EOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    record = _denials(project)[0]
    assert len(record["target"]) == 400, f"cap not applied: {len(record['target'])}"
    assert record.get("truncated") is True


# --- secrets in the target ---------------------------------------------------


def test_a_secret_in_the_target_is_redacted(gate, project: Path) -> None:
    """The journal is the first place a command line reaches; §10 applies there too.

    The command has to be a WRITE for a record to exist at all, so the credential rides in a
    heredoc being appended to a source file — a shape that really happens when an agent
    writes a config. The value must never appear in the file, and the rule must be named so a
    reader knows why the field is masked.
    """
    value = "abcdefghijklmnop"
    command = (
        "cat >> src/service.py <<'EOF'\n"
        f"AUTHORIZATION: Bearer {value}\n"
        "EOF"
    )
    gate.decide(ledger=project, tool="shell", args={"command": command})
    text = helpers.journal_path(project).read_text(encoding="utf-8")
    assert value not in text, "the credential value reached the journal"
    record = _denials(project)[0]
    assert record["target"].startswith("<redacted"), record["target"]


def test_an_ordinary_long_path_is_not_masked(gate, project: Path) -> None:
    """A path is not a credential, and the `base64` rule cannot tell them apart.

    Its pattern is `[A-Za-z0-9+/]{40,}` and `/` is in that alphabet, so any absolute path
    longer than 40 characters matches. Masking it would leave the field saying nothing,
    which is the opposite of what it is for. Measured on a real temp path (TASK-086 F25).
    """
    long_path = str(project / "src" / "service.py")
    assert len(long_path) > 40, "the fixture path is too short to prove the point"
    gate.decide(ledger=project, tool="write", args={"file_path": long_path})
    record = _denials(project)[0]
    assert record["target"] == long_path, record["target"]


def test_a_real_token_in_a_command_line_is_still_masked(gate, project: Path) -> None:
    """The exemption is for a path, not for every target: a token shape still masks."""
    fake_github_token = "ghp_" + "a" * 30
    command = f"cat > src/service.py <<'EOF'\n{fake_github_token}\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    record = _denials(project)[0]
    assert record["target"].startswith("<redacted"), record["target"]


# --- phase and rule ----------------------------------------------------------


def test_a_denial_records_the_phase_it_happened_in(gate, project: Path) -> None:
    """Phase 1 refuses an edit; the same edit is allowed in phase 4. The record must say which."""
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    record = _denials(project)[0]
    assert record.get("phase") == 1


def test_phase_four_and_phase_one_records_differ(gate, project: Path) -> None:
    """Two refusals under different phases must not look identical.

    In phase 4 the refusal is about the missing `Working on:` marker, not about the phase —
    so both `phase` and `verdict_of` differ between the two records.
    """
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    helpers.set_phase(project, 4)
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    records = _denials(project)
    assert len(records) == 2
    assert records[0]["phase"] == 1
    assert records[1]["phase"] == 4
    assert records[0]["verdict_of"] != records[1]["verdict_of"]


def test_verdict_of_names_the_rule_not_the_wording(gate, project: Path) -> None:
    """Two rules whose text starts identically must still be distinguishable.

    The class was recovered by parsing `why`, which breaks silently on any reword. A stable
    identifier is the point of the field.
    """
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    phase_refusal = _denials(project)[0]
    gate.decide(ledger=project, tool="write", args={"file_path": "/outside-a/x.py"})
    outside_refusal = _denials(project)[1]
    assert phase_refusal["verdict_of"] == "phase-ban"
    assert outside_refusal["verdict_of"] == "outside-project"


def test_the_ledger_a_refusal_belongs_to_is_named(gate, project: Path) -> None:
    """F9: a record used to land wherever mtime pointed. The record must say whose it is."""
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    record = _denials(project)[0]
    assert record.get("ledger") == str(project.resolve())


# --- compatibility -----------------------------------------------------------


def test_a_record_without_the_new_fields_still_reads(journal, project: Path) -> None:
    """Old journals stay readable: the new fields are not in `REQUIRED_FIELDS`.

    There are 133 such records on disk in this repository. A migration requirement would
    mean every existing journal has to be rewritten before the gate works again.
    """
    assert journal.REQUIRED_FIELDS == ("at", "event", "why")
    helpers.append(project, event="deny", tool="shell", why="phase 1 forbids this")
    events = journal.read_events(project)
    assert len(events) == 1
    assert journal.state_of(project)["phase"] == 1
