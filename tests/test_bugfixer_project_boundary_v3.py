"""The project boundary and the copy bypass (spec invariants 43–44, added 2026-11-27).

Two rules the live Cursor run (2026-11-27) forced into the spec, both measured before they were
written:

* **43 — writes stay inside the project.** The plugin works in the open project and its
  subdirectories. `/tmp` and every other system directory are refused in every phase, including
  the probe phase: a fix belongs to the project, and a temporary file belongs in the project's
  own dump directory. A person who needs a system file changed does it themselves; the agent
  prints the command.
* **44 — copying is not a way around the gate.** `rsync`, `tar`, `cpio`, `unzip` and `scp` write
  files, and were classified as reads, so `rsync -a src/ scratch/` moved a tree past the phase
  ban. That is a bypass of every rule above, and it was reproduced before it was fixed.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_HOOKS = jrnl.HOOKS

CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)


@pytest.fixture()
def gate():
    """The gate module, imported as the plugin ships it."""
    if str(_HOOKS) not in sys.path:
        sys.path.insert(0, str(_HOOKS))
    return importlib.import_module("bugfixer_gate")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project in phase 1 with a contract and a ledger."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    ledger = tmp_path / "tasks" / "TASK-1.md"
    ledger.write_text(
        "## Task\nfix\n\n## Clarifications\nQ: a  A: b\nQ: c  A: d\nQ: e  A: f\n\n"
        "FAILING TEST: pytest -> 1 failed\nBASELINE: 5/5\n",
        encoding="utf-8",
    )
    jrnl.set_phase(ledger, 1)
    return ledger


def _verdict(gate, ledger: Path, tool: str, args: dict) -> object:
    """Ask the gate for a decision on one call."""
    return gate.decide(ledger=ledger, tool=tool, args=args)


# --- invariant 43: the project is the boundary ------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "/outside-a/scratch.py",
        "/outside-b/conf",
        "/outside-c/bin/tool",
        "/outside-d/tmp/x",
        "/outside-e/thing/conf",
    ],
)
def test_a_write_into_a_system_directory_is_refused(gate, project: Path, target: str) -> None:
    """No phase opens these: the reason names the boundary, not phase 1.

    The targets are deliberately fictional. A real system path in a test names a file the operator
    owns, and a reader cannot tell from the diff that nothing was written — see
    `.cursor/rules/no-dangerous-commands.mdc`. What the gate is asked here is only "is this path
    outside the project", and any absolute path answers that.
    """
    verdict = _verdict(gate, project, "write", {"file_path": target})
    assert not verdict.allow
    assert "outside the project" in verdict.reason
    assert target in verdict.reason


def test_the_boundary_is_not_a_phase_rule(gate, project: Path) -> None:
    """Told "phase 1 forbids this", an agent would wait for phase 4 and try again.

    Phase 4 is where production edits are allowed, so the out-of-project check must come first
    and name its own reason.
    """
    jrnl.set_phase(project, 4)
    verdict = _verdict(gate, project, "write", {"file_path": "/outside-a/scratch.py"})
    assert not verdict.allow
    assert "outside the project" in verdict.reason
    assert "Phase 4" not in verdict.reason


def test_the_refusal_says_where_temporary_files_go(gate, project: Path) -> None:
    """A refusal that only says "no" leaves the agent with nothing to do instead."""
    verdict = _verdict(gate, project, "write", {"file_path": "/outside-a/x"})
    assert "Raw dumps" in verdict.reason or "task file" in verdict.reason
    assert "print the command" in verdict.reason


def test_a_write_inside_the_project_is_judged_by_the_phase_rule(gate, project: Path) -> None:
    """The negative control: the boundary must not swallow the ordinary case."""
    verdict = _verdict(gate, project, "write", {"file_path": "src/a.py"})
    assert not verdict.allow
    assert "Phase 1" in verdict.reason


def test_a_test_write_inside_the_project_is_still_allowed(gate, project: Path) -> None:
    """Phase 1 exists to write failing tests; the boundary must not block them."""
    verdict = _verdict(gate, project, "write", {"file_path": "tests/test_a.py"})
    assert verdict.allow, verdict.reason


def test_an_absolute_path_inside_the_project_is_inside(gate, project: Path) -> None:
    """An agent may address its own project absolutely; that is not "outside"."""
    inside = project.parent / "tests" / "test_b.py"
    verdict = _verdict(gate, project, "write", {"file_path": str(inside)})
    assert verdict.allow, verdict.reason


def test_a_shell_redirection_outside_the_project_is_refused(gate, project: Path) -> None:
    """The ban is about the destination, whatever tool carries it."""
    verdict = _verdict(gate, project, "shell", {"command": "echo x > /outside-a/x"})
    assert not verdict.allow
    assert "outside the project" in verdict.reason


# --- invariant 44: copying is a write ---------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rsync -a src/ scratch/", True),
        ("rsync -a --delete src/ scratch/", True),
        ("tar xf a.tgz", True),
        ("tar czf b.tgz src/", True),
        ("unzip a.zip -d out", True),
        ("scp file host:/outside-a/x", True),
        ("cpio -t < listing", False),
        ("unzip -l a.zip", False),
        ("tar --version", False),
        ("pytest -q 2>/dev/null", False),
    ],
)
def test_the_archivers_and_copy_tools_are_classified_correctly(
    gate, command: str, expected: bool
) -> None:
    """A tree moved by `rsync` is a tree changed; a listing is not.

    The read-only exclusions are limited to the unambiguous long flags: a bare `tar tzf` stays
    classified as a write, because a false deny is noisy and a false allow is a bypass.
    """
    is_write, _ = gate._is_write_attempt("shell", {"command": command})
    assert is_write is expected, f"{command} classified as write={is_write}"


def test_rsync_out_of_the_project_is_refused_end_to_end(gate, project: Path) -> None:
    """The bypass as it was found: the whole tree, past the phase ban, no refusal."""
    verdict = _verdict(gate, project, "shell", {"command": "rsync -a src/ /outside-a/scratch/"})
    assert not verdict.allow
    assert "/outside-a/scratch/" in verdict.reason


def test_rsync_inside_the_project_is_still_a_phase_matter(gate, project: Path) -> None:
    """Copying inside the project is a normal edit, judged by the phase like any other."""
    verdict = _verdict(gate, project, "shell", {"command": "rsync -a src/ backup/"})
    assert not verdict.allow
    assert "Phase 1" in verdict.reason


# --- the refusal is a request to the person ---------------------------------


def test_the_out_of_project_refusal_asks_the_user(gate, project: Path) -> None:
    """Cursor does not enforce `ask` (docs, 2026-11), so the only honest form is a deny that
    tells the agent to ask. Silently allowing would be the failure §0 names.
    """
    verdict = _verdict(gate, project, "write", {"file_path": "/outside-a/x"})
    lowered = verdict.reason.lower()
    assert "user" in lowered or "ask" in lowered
    assert json.dumps({"permission": "deny"})
