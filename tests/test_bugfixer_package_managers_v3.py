"""A package manager with a mutating verb is a write (F29, spec §5.5).

Measured defect: `_is_write_command` returned ``False`` for `poetry add requests`, `pip install
numpy`, `npm install`, `poetry lock` — the gate classified **none** of the 12 mutating segments
found in 601 real package-manager segments. So a dependency could be added in phase 1 with no
hypothesis, and a lockfile rewritten with nothing recording that the configuration changed.

The rule is on the (program, verb) pair, because `poetry run` is the main way to start a script in
this repository — 457 of 472 reading segments — and a rule on the program name alone would have
refused it. `--dry-run` is documented by Poetry for `install`/`add`/`remove`/`update` and makes the
command a read; `lock` has no dry-run.

Every test here fails against the code before the change, except the boundary ones, which fail if
the change over-reaches.
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

UNVERIFIED_CONTRACT = (
    "Contract version: 1\nContract verified by: not yet\n\n"
    "## Ledger\n\nTask/bug file: TASK-*.md\nRaw dumps: logs/dumps/\n\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
)


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
    return _load("bugfixer_gate_package_managers", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a contract and a ledger, in phase 1.

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


# --- the defect: adding a dependency was a read -------------------------------


@pytest.mark.parametrize(
    "command",
    (
        "poetry add requests",
        "poetry remove requests",
        "poetry install",
        "poetry lock",
        "poetry lock --no-update",
        "poetry update",
        "pip install numpy",
        "python3 -m pip install numpy",
        ".venv/bin/pip install ruff",
        "npm install",
        "cargo add serde",
        "go get example.com/x",
        "go mod tidy",
        "gem install rails",
        "composer require x/y",
        "uv add requests",
    ),
)
def test_a_mutating_package_manager_command_is_a_write(gate, command: str) -> None:
    """Installing, adding, removing, locking — every one of these writes a manifest or a lockfile."""
    assert gate._is_write_command(command), f"{command} is still classified as a read"


def test_adding_a_dependency_is_refused_in_phase_one(gate, project: Path) -> None:
    """The end the classification serves: no dependency change without a hypothesis."""
    verdict = gate.decide(
        ledger=project, tool="shell", args={"command": "poetry add requests"}
    )
    assert not verdict.allow
    assert verdict.verdict_of == "phase-ban"


def test_a_lockfile_rewrite_is_refused_in_phase_one(gate, project: Path) -> None:
    """`poetry lock --no-update` still refreshes the lockfile, so it is not a read."""
    verdict = gate.decide(
        ledger=project, tool="shell", args={"command": "poetry lock --no-update"}
    )
    assert not verdict.allow
    assert verdict.verdict_of == "phase-ban"


# --- the boundary: what must not be touched ---------------------------------


@pytest.mark.parametrize(
    "command",
    (
        "poetry run python scripts/x.py",
        "poetry run pytest -q",
        "poetry show",
        "poetry check",
        "poetry env info",
        "poetry --version",
        "pip list",
        "pip freeze",
        "npm ls",
        "cargo build",
        "go test ./...",
    ),
)
def test_a_reading_or_wrapping_command_stays_a_read(gate, command: str) -> None:
    """`poetry run` is how a script is started here; refusing it would break the workflow.

    Also checked: `cargo build` writes `target/` but not the manifest, and the contract's `logs/`
    rule already covers build output.
    """
    assert not gate._is_write_command(command), f"{command} became a write"


@pytest.mark.parametrize(
    "command",
    (
        "poetry install --dry-run",
        "poetry add --dry-run requests",
        "poetry update --dry-run",
    ),
)
def test_a_documented_dry_run_stays_a_read(gate, command: str) -> None:
    """Poetry documents `--dry-run` as "outputs the operations but will not execute anything".

    Checked against python-poetry.org/docs/cli on 2026-11-28: poetry is not installed on this
    machine, so the flag list could not be taken from `--help`.
    """
    assert not gate._is_write_command(command), f"{command} became a write"


def test_a_command_line_writes_if_any_segment_does(gate) -> None:
    """`cd x && poetry add y` is one line and the second segment writes."""
    assert gate._is_write_command("cd /repo && poetry add x")
    assert gate._is_write_command("pytest -q && poetry install")


def test_quoted_package_manager_text_is_data(gate) -> None:
    """`grep 'poetry add x' file` searches for a string; only the redirect would write."""
    assert not gate._is_write_command("grep 'poetry add x' notes.txt")
    assert not gate._is_write_command("rg 'pip install numpy' README.md")


def test_a_bare_program_name_is_not_a_write(gate) -> None:
    """`poetry` with no verb prints help; there is nothing to classify and nothing to refuse.

    Measured as the one uncovered branch of this change: the loop must answer "no verb, no write"
    rather than fall through, and `poetry --version` covers the flag case separately.
    """
    assert not gate._is_write_command("poetry")
    assert not gate._is_write_command("pip")


# --- interaction with the linter window (ST-11) ------------------------------


def test_the_linter_window_still_opens_the_linter_install(tmp_path: Path) -> None:
    """F29 must not close the window it was found through.

    The window is checked before the phase rules, so an install passes during initialisation
    **because it is named there**, not because the gate failed to classify it — which is what the
    note in F29 warned about. Since the operator's ruling of 2026-11-28 14:20 the window covers
    any dependency, not only the linter.
    """
    (tmp_path / "bug-fixer.project.md").write_text(UNVERIFIED_CONTRACT, encoding="utf-8")
    for name in ("src", "tests", "logs/dumps", "scripts"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "TASK-999.md").write_text(BASELINE, encoding="utf-8")

    def ask(command: str) -> str:
        payload = {"tool_name": "Shell", "tool_input": {"command": command}}
        import subprocess  # noqa: PLC0415

        result = subprocess.run(
            [sys.executable, str(helpers.SKILL_DIR / "bin" / "bugfixer"), "gate"],
            input=json.dumps(payload), text=True, capture_output=True, check=False, cwd=tmp_path,
        )
        try:
            return str(json.loads(result.stdout).get("permission", "?"))
        except json.JSONDecodeError:
            return f"unparsed:{result.stdout[:40]}"

    assert ask("poetry add --group dev ruff") == "allow", "the named window must still open"
    # The boundary is the form, not the package name: setup and dependencies are one permission.
    assert ask("poetry add requests") == "allow", "environment setup includes dependencies"
    assert ask("poetry add x; rm -rf src") == "deny", "but never a chained command"
