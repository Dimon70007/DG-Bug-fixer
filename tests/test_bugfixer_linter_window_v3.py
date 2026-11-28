"""The linter is installed during initialisation, and the window is narrow (spec §5.6).

Operator decision (TASK-086, 2026-11-28 13:11): the linter is checked, installed and configured
during plugin initialisation — Phase 0a — not during a probe. Measured obstacle: while
`Contract verified by:` still says `not yet`, the gate opens only the contract and the plugin
tree, so `ruff --version` and `poetry add ruff` were refused like everything else.

The window that fixes this is a list of **allowed forms**, not a general "during setup" pass.
The reason is measured (TASK-086 F29): the gate does not classify `poetry add` as a write at
all, so it cannot tell `poetry add ruff` from `poetry add requests` by read-versus-write. The
package name in the form is the only thing that can, and `poetry run` — 457 of the 472 package
manager segments in this repository's transcripts — must keep working.

Every test here fails against the code before the window.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_GATE = helpers.HOOKS / "bugfixer_gate.py"

UNVERIFIED_CONTRACT = (
    "Contract version: 1\nContract verified by: not yet\n\n"
    "## Ledger\n\nTask/bug file: TASK-*.md\nRaw dumps: logs/dumps/\n\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
)

LEDGER = (
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
    return _load("bugfixer_gate_linter_window", _GATE)


@pytest.fixture()
def stand(tmp_path: Path) -> Path:
    """A project mid-initialisation: contract present, unverified, ledger present.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        Project directory.
    """
    (tmp_path / "bug-fixer.project.md").write_text(UNVERIFIED_CONTRACT, encoding="utf-8")
    for name in ("src", "tests", "logs/dumps", "scripts"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "TASK-999.md").write_text(LEDGER, encoding="utf-8")
    return tmp_path


def _gate_says(stand: Path, tool: str, args: dict) -> str:
    """Run the real gate CLI inside the stand.

    Args:
        stand: Project directory, used as the working directory.
        tool: Harness tool name.
        args: Tool arguments.

    Returns:
        The permission string: ``allow`` or ``deny``.
    """
    import subprocess  # noqa: PLC0415 — one call site, and importing it at module level would
    # hide the subprocess from the suite's process-tracking fixture.

    payload = {"tool_name": tool, "tool_input": args}
    result = subprocess.run(
        [sys.executable, str(helpers.SKILL_DIR / "bin" / "bugfixer"), "gate"],
        input=json.dumps(payload), text=True, capture_output=True, check=False, cwd=stand,
    )
    try:
        return str(json.loads(result.stdout).get("permission", "?"))
    except json.JSONDecodeError:
        return f"unparsed:{result.stdout[:40]}"


# --- the steps the operator asked for, executed where they belong ------------


def test_checking_for_the_linter_is_open_during_init(stand: Path) -> None:
    """Step 1 of §5.6: ask whether the linter exists. It is a read, and it was refused."""
    assert _gate_says(stand, "Shell", {"command": "ruff --version"}) == "allow"
    assert _gate_says(stand, "Shell", {"command": "shellcheck --version"}) == "allow"


def test_installing_the_linter_is_open_during_init(stand: Path) -> None:
    """Step 2: install when missing. The package name is pinned, which is the whole point."""
    assert _gate_says(stand, "Shell", {"command": "poetry add --group dev ruff"}) == "allow"
    assert _gate_says(stand, "Shell", {"command": "pip install ruff"}) == "allow"


def test_configuring_the_linter_is_open_during_init(stand: Path) -> None:
    """Step 3: write `ruff.toml`, or a `[tool.ruff]` table into `pyproject.toml`."""
    assert _gate_says(
        stand, "Write", {"file_path": str(stand / "ruff.toml"), "content": "line-length = 100\n"}
    ) == "allow"
    assert _gate_says(
        stand,
        "Write",
        {"file_path": str(stand / "pyproject.toml"), "content": "[tool.ruff]\nline-length = 100\n"},
    ) == "allow"


def test_running_the_linter_on_a_script_is_open_during_init(stand: Path) -> None:
    """Step 4: the check itself, so a script can be linted before it is first run."""
    assert _gate_says(stand, "Shell", {"command": "ruff check scripts/x.py"}) == "allow"
    assert _gate_says(stand, "Shell", {"command": "shellcheck scripts/x.sh"}) == "allow"


# --- the window is narrow: each case is paired with its near-miss ------------


def test_a_different_package_is_not_installed_through_the_window(gate) -> None:
    """`poetry add ruff` and `poetry add requests` differ only in the package, and that is enough.

    Measured F29: the gate does not classify either as a write, so read-versus-write cannot
    separate them. The form can.
    """
    assert gate._is_linter_window("shell", {"command": "poetry add --group dev ruff"})
    assert not gate._is_linter_window("shell", {"command": "poetry add requests"})
    assert not gate._is_linter_window("shell", {"command": "poetry add ruff requests"})
    assert not gate._is_linter_window("shell", {"command": "pip install numpy"})


def test_chaining_another_command_closes_the_window(gate) -> None:
    """A form is one call: `ruff --version && poetry add requests` is not one of the list."""
    assert not gate._is_linter_window(
        "shell", {"command": "ruff --version && poetry add requests"}
    )
    assert not gate._is_linter_window("shell", {"command": "ruff check x.py; rm -rf src"})
    assert not gate._is_linter_window("shell", {"command": "sudo poetry add ruff"})


def test_reformatting_sources_is_not_a_lint_check(gate) -> None:
    """`ruff format` rewrites the tree, so it is not step 4 and must not inherit its permission."""
    assert gate._is_linter_window("shell", {"command": "ruff check scripts/x.py"})
    assert not gate._is_linter_window("shell", {"command": "ruff format src/service.py"})


def test_a_multiline_command_never_matches(gate) -> None:
    """A newline is where a single form ends; accepting it would let a second command ride along."""
    assert not gate._is_linter_window(
        "shell", {"command": "ruff --version\nrm -rf src"}
    )


def test_a_pyproject_write_without_the_table_is_not_configuration(gate, stand: Path) -> None:
    """`pyproject.toml` is ordinary configuration; only the linter's own table is the window."""
    assert gate._is_linter_window(
        "write", {"file_path": "pyproject.toml", "content": "[tool.ruff.lint]\n"}
    )
    assert not gate._is_linter_window(
        "write", {"file_path": "pyproject.toml", "content": "[tool.poetry]\nname = 'p'\n"}
    )


def test_the_window_does_not_open_ordinary_work(gate, stand: Path) -> None:
    """The neighbouring rules still apply: the window is not a general setup pass."""
    assert _gate_says(stand, "Shell", {"command": "pytest -q"}) == "deny"
    assert _gate_says(stand, "Shell", {"command": "rm -rf src"}) == "deny"
    assert _gate_says(stand, "Shell", {"command": "poetry run python scripts/x.py"}) == "deny"


def test_an_installed_project_gets_no_new_permission_from_the_window(stand: Path) -> None:
    """After the contract is confirmed the window is irrelevant: phase rules decide again.

    `ruff.toml` is configuration, so in phase 1 it is refused as before — the window belongs to
    initialisation, and it must not leak into a running fix.
    """
    contract = stand / "bug-fixer.project.md"
    contract.write_text(
        UNVERIFIED_CONTRACT.replace("not yet", "agent 2026-01-01 — test"), encoding="utf-8"
    )
    assert _gate_says(
        stand, "Write", {"file_path": str(stand / "ruff.toml"), "content": "line-length = 100\n"}
    ) == "deny"


def test_the_window_also_opens_before_any_contract_exists(tmp_path: Path) -> None:
    """A project being initialised for the first time has no contract yet, and that is step 0.1.

    Two refusal branches, one rule: "no contract at all" and "contract not yet verified" both
    stop the run before any phase rule, so the window has to be checked in both. Without this
    test the second branch is unexercised — measured as the only uncovered lines of the change.
    """
    for name in ("src", "tests", "logs/dumps", "scripts"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    assert _gate_says(tmp_path, "Shell", {"command": "ruff --version"}) == "allow"
    assert _gate_says(tmp_path, "Shell", {"command": "poetry add --group dev ruff"}) == "allow"
    # And the branch is still a refusal for everything else.
    assert _gate_says(tmp_path, "Shell", {"command": "poetry add requests"}) == "deny"
