"""Environment setup in Phase 0a, and where its boundary is (spec §5.6, invariant 37).

Operator's ruling (TASK-086, 2026-11-28 14:20): **everywhere environment setup is allowed,
installing a dependency is allowed too.** The two are one act — `poetry add x` and a hand-edited
`pyproject.toml` change the same file — so a window that opened only the linter was inconsistent
with the manifest edit it already permitted.

This module replaced the linter-only window tests. What it keeps from them: the window is a list of
**whole forms**, one command, no chaining, no `sudo`. What it changes: any dependency may be
installed, not only `ruff`.

Every test here fails against the code before the widening, except the boundary ones, which fail if
the widening goes too far.
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
    return _load("bugfixer_gate_env_window", _GATE)


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
        The permission string.
    """
    import subprocess  # noqa: PLC0415 — one call site.

    payload = {"tool_name": tool, "tool_input": args}
    result = subprocess.run(
        [sys.executable, str(helpers.SKILL_DIR / "bin" / "bugfixer"), "gate"],
        input=json.dumps(payload), text=True, capture_output=True, check=False, cwd=stand,
    )
    try:
        return str(json.loads(result.stdout).get("permission", "?"))
    except json.JSONDecodeError:
        return f"unparsed:{result.stdout[:40]}"


# --- the ruling: setup and dependencies are one permission -------------------


@pytest.mark.parametrize(
    "command",
    (
        "poetry add requests",
        "poetry add --group dev mypy",
        "poetry install",
        "poetry lock",
        "pip install numpy",
        "python3 -m pip install -r requirements.txt",
    ),
)
def test_installing_a_dependency_is_open_during_init(stand: Path, command: str) -> None:
    """Where environment setup is allowed, installing a dependency is allowed.

    Before the ruling only `ruff`/`shellcheck-py` passed, while `pyproject.toml` could already be
    edited by hand — two expressions of the same act with different answers.
    """
    assert _gate_says(stand, "Shell", {"command": command}) == "allow"


def test_editing_the_manifest_is_open_during_init(stand: Path) -> None:
    """The hand-edited form of the same act, so the two never disagree again."""
    assert _gate_says(
        stand,
        "Write",
        {"file_path": str(stand / "pyproject.toml"),
         "content": "[tool.poetry.dependencies]\nrequests = \"^2\"\n"},
    ) == "allow"


def test_the_linter_check_is_still_open(stand: Path) -> None:
    """§5.6's last step: the script is linted before it runs, so the check must be permitted."""
    assert _gate_says(stand, "Shell", {"command": "ruff --version"}) == "allow"
    assert _gate_says(stand, "Shell", {"command": "ruff check scripts/x.py"}) == "allow"


# --- the boundary: widening is not removing ----------------------------------


def test_a_chained_command_closes_the_window(gate) -> None:
    """A form is one command; the second half is what the window must never cover."""
    assert gate._is_env_setup_window("shell", {"command": "poetry add requests"})
    assert not gate._is_env_setup_window("shell", {"command": "poetry add x && rm -rf src"})
    assert not gate._is_env_setup_window("shell", {"command": "poetry add x; rm -rf src"})
    assert not gate._is_env_setup_window("shell", {"command": "sudo poetry add x"})


def test_a_multiline_command_never_matches(gate) -> None:
    """A newline is where one form ends."""
    assert not gate._is_env_setup_window("shell", {"command": "poetry add x\nrm -rf src"})


def test_running_a_script_is_not_setup(gate) -> None:
    """`poetry run` starts a script; it does not prepare the environment."""
    assert not gate._is_env_setup_window("shell", {"command": "poetry run pytest"})


def test_reformatting_sources_is_not_a_lint_check(gate) -> None:
    """`ruff format` rewrites the tree, so it is not the check §5.6 requires."""
    assert gate._is_env_setup_window("shell", {"command": "ruff check scripts/x.py"})
    assert not gate._is_env_setup_window("shell", {"command": "ruff format src/service.py"})


def test_a_pyproject_write_needs_a_tool_table(gate) -> None:
    """`pyproject.toml` is configuration; the window covers the tables that configure it."""
    assert gate._is_env_setup_window(
        "write", {"file_path": "pyproject.toml", "content": "[tool.poetry.dependencies]\nx = 1\n"}
    )
    assert gate._is_env_setup_window(
        "write", {"file_path": "pyproject.toml", "content": "[tool.ruff]\nline-length = 100\n"}
    )
    assert not gate._is_env_setup_window(
        "write", {"file_path": "pyproject.toml", "content": "random = 1\n"}
    )


def test_the_window_does_not_open_source_or_environment_secrets(gate, stand: Path) -> None:
    """The window is about installing and configuring, not about editing the project."""
    assert not gate._is_env_setup_window(
        "write", {"file_path": "src/service.py", "content": "x = 2\n"}
    )
    assert not gate._is_env_setup_window("write", {"file_path": ".env", "content": "X=1\n"})
    assert _gate_says(stand, "Shell", {"command": "rm -rf src"}) == "deny"


def test_the_window_also_opens_before_any_contract_exists(tmp_path: Path) -> None:
    """A project being initialised for the first time has no contract yet, and that is step 0.1.

    Two refusal branches, one rule: "no contract at all" and "contract not yet verified" both stop
    the run before any phase rule, so the window is checked in both.
    """
    for name in ("src", "tests", "logs/dumps", "scripts"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    assert _gate_says(tmp_path, "Shell", {"command": "poetry add requests"}) == "allow"
    assert _gate_says(tmp_path, "Shell", {"command": "poetry add x; rm -rf src"}) == "deny"


def test_an_installed_project_gets_no_new_permission_from_the_window(stand: Path) -> None:
    """After the contract is confirmed the window is irrelevant: phase rules decide again.

    A dependency installed in phase 1 is a change to behaviour, and that is what the phase ban is
    for — the window belongs to initialisation, not to a running fix.
    """
    contract = stand / "bug-fixer.project.md"
    contract.write_text(
        UNVERIFIED_CONTRACT.replace("not yet", "agent 2026-01-01 — test"), encoding="utf-8"
    )
    assert _gate_says(stand, "Shell", {"command": "poetry add requests"}) == "deny"
    assert _gate_says(
        stand, "Write", {"file_path": str(stand / "ruff.toml"), "content": "line-length = 100\n"}
    ) == "deny"
