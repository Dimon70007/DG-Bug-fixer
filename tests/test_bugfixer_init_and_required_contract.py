"""Starting a plugin run without a contract: scaffold a real one, then refuse.

The operator's position, stated plainly: a gate that cannot be configured should be removed
rather than left silent, and running the plugin with no contract at all is pointless — the
agent would not know what is expected of it, where findings go, or which command runs the
tests. A ready default contract is better than allowing work with none.

Spec §6 phase 0 already says the same thing in one line: "если контракта нет — **спросить и
создать** `bug-fixer.project.md`". This stage supplies the tool that line assumed:

* `bugfixer init` writes a contract from the template, with the fields it can safely infer
  (ledger location, test paths) filled in and every value it cannot infer left as a visible
  placeholder;
* until the placeholders are replaced, the gate refuses work — a contract that still contains
  `<…>` is a contract nobody has read;
* with no contract at all, the gate refuses and says how to create one.

The one thing that does not change: work outside a plugin run. Nothing is wired in a project
that has not installed the plugin, so nothing there is refused.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_SKILL = jrnl.SKILL_DIR
_CLI = _SKILL / "bin" / "bugfixer"
_GATE = _SKILL / "hooks" / "bugfixer_gate.py"

_WRITE = {"tool_name": "Write", "tool_input": {"file_path": "src/core.py"}}


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the plugin CLI."""
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(cwd) if cwd else None,
    )


def _run_gate(payload: dict, project: Path) -> subprocess.CompletedProcess[str]:
    """Run the gate the way a harness does, from the project directory."""
    return subprocess.run(
        [sys.executable, str(_GATE)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        cwd=str(project),
    )


# --- init: a contract that is real, not a blank page ------------------------


def test_init_writes_a_contract_the_schema_recognises(tmp_path: Path) -> None:
    """The file must be at the path the skill looks for, and must parse."""
    result = _run_cli("init", "--project", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    contract = tmp_path / "bug-fixer.project.md"
    assert contract.is_file(), "init did not create the contract at the documented path"
    assert "Task/bug file:" in contract.read_text(encoding="utf-8")


def test_init_fills_in_what_it_can_prove(tmp_path: Path) -> None:
    """A test directory and a task directory are facts on disk, not guesses.

    ``§0`` forbids guessing a command; it does not forbid reading the filesystem. Filling in
    what exists removes the two placeholders that cause the most silent breakage.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    result = _run_cli("init", "--project", str(tmp_path))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "Test paths: tests/" in text, text
    assert "Task/bug file: tasks/" in text, text


def test_init_leaves_unprovable_values_as_visible_placeholders(tmp_path: Path) -> None:
    """The test command cannot be inferred, and inventing one is the failure §0 names.

    It stays a `<…>` placeholder, which the validator treats as missing — so the contract
    cannot be used until a human states the command.
    """
    result = _run_cli("init", "--project", str(tmp_path))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "Full suite: <" in text, "the test command was invented instead of asked for"


def test_init_refuses_to_overwrite_an_existing_contract(tmp_path: Path) -> None:
    """A filled contract holds decisions; replacing it silently would destroy them."""
    contract = tmp_path / "bug-fixer.project.md"
    contract.write_text("# hand-written\nFull suite: make test\n", encoding="utf-8")
    result = _run_cli("init", "--project", str(tmp_path))
    assert result.returncode == 2
    assert "exists" in (result.stdout + result.stderr).lower()
    assert contract.read_text(encoding="utf-8").startswith("# hand-written")


def test_init_can_force_after_an_explicit_request(tmp_path: Path) -> None:
    """Overwriting is allowed when asked for by name, never by accident."""
    contract = tmp_path / "bug-fixer.project.md"
    contract.write_text("# hand-written\n", encoding="utf-8")
    result = _run_cli("init", "--project", str(tmp_path), "--force")
    assert result.returncode == 0, result.stderr
    assert "Task/bug file:" in contract.read_text(encoding="utf-8")


def test_a_freshly_initialised_contract_is_not_yet_usable(tmp_path: Path) -> None:
    """`doctor` must say what is missing, rather than bless a contract nobody finished."""
    assert _run_cli("init", "--project", str(tmp_path)).returncode == 0
    result = _run_cli("doctor", "--project", str(tmp_path))
    assert result.returncode == 2, "an unfinished contract was reported as usable"
    assert "Full suite" in result.stdout + result.stderr


# --- the gate: a run without a usable contract is refused -------------------


def test_the_gate_refuses_a_project_with_no_contract(tmp_path: Path) -> None:
    """The operator's rule: no contract means no run, and the refusal says how to start one.

    This is the reverse of the old behaviour, which allowed everything. A plugin whose whole
    job is to constrain the agent cannot do that job without being told what to constrain.
    """
    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2, (
        f"a project with no contract let a production write through: {result.stdout}"
    )
    message = json.loads(result.stdout)["agent_message"]
    assert "bugfixer init" in message, f"the refusal does not say how to create one: {message}"


def test_the_gate_refuses_a_contract_with_placeholders(tmp_path: Path) -> None:
    """A contract nobody finished is not a contract: the agent would be reading `<…>`."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: <e.g. make test>\nTest paths: tests/\n",
        encoding="utf-8",
    )
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2
    message = json.loads(result.stdout)["agent_message"]
    assert "Full suite" in message, message


def test_a_completed_contract_lets_the_gate_work_normally(tmp_path: Path) -> None:
    """The legal path stays open: a real contract gives a real verdict, not a blanket refusal."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n"
        "Allowed: none\n\n## Extra prohibitions\n\n- no sudo\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2
    # A real rule, not the contract check: phase 1 forbids production edits.
    assert "Phase 1" in json.loads(result.stdout)["agent_message"]


def test_creating_the_contract_itself_is_never_refused(tmp_path: Path) -> None:
    """`bugfixer init` must be able to write the file the gate is asking for.

    Without this the refusal would be a lockout: the gate demands a contract, and the write
    that creates it is refused by the same gate.
    """
    result = _run_gate(
        {"tool_name": "Write", "tool_input": {"file_path": "bug-fixer.project.md"}},
        tmp_path,
    )
    assert result.returncode == 0, (
        f"the gate refused the contract it demands: {result.stdout}"
    )


def test_installing_the_plugin_is_still_possible_without_a_contract(tmp_path: Path) -> None:
    """Setup writes must not be blocked before a contract exists, or setup can never happen.

    The installer writes its own tree into ``.cursor/skills``; refusing that would make the
    documented install path impossible in a project that has not been initialised yet.
    """
    result = _run_gate(
        {"tool_name": "Write", "tool_input": {"file_path": ".cursor/skills/bug-fixer/SKILL.md"}},
        tmp_path,
    )
    assert result.returncode == 0, (
        f"the install was blocked by the gate it installs: {result.stdout}"
    )


# --- out of the box: installing into a project brings a starting contract ----


def test_a_project_install_writes_a_starting_contract(tmp_path: Path) -> None:
    """The operator's requirement: the plugin works out of the box.

    A gate that refuses without a contract, and an install that leaves no contract behind, is a
    plugin that refuses everything the moment it is installed. So the project-scope install
    writes one — the same scaffold `bugfixer init` would write, with the facts it can prove
    filled in and the decisions left visible.
    """
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("installer_contract_test", _SKILL / "install.py")
    assert spec and spec.loader
    installer = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)

    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    plan = installer.build_plans(["cursor"], project=str(tmp_path))[0]
    installer.install(plan, dry_run=False)

    contract = tmp_path / "bug-fixer.project.md"
    assert contract.is_file(), "the install left the project without a contract, so the gate refuses"
    text = contract.read_text(encoding="utf-8")
    assert "Task/bug file: tasks/TASK-*.md" in text
    assert "Test paths: tests/" in text


def test_a_project_install_does_not_overwrite_a_hand_written_contract(tmp_path: Path) -> None:
    """Re-installing must not destroy the decisions a project already made."""
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("installer_contract_keep", _SKILL / "install.py")
    assert spec and spec.loader
    installer = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)

    contract = tmp_path / "bug-fixer.project.md"
    contract.write_text("# mine\nFull suite: make test\n", encoding="utf-8")
    plan = installer.build_plans(["cursor"], project=str(tmp_path))[0]
    installer.install(plan, dry_run=False)

    assert contract.read_text(encoding="utf-8").startswith("# mine")

