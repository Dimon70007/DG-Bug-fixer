"""Portability of the skill install (TASK-081 deployment).

The skill has to work on a machine it has never seen: no absolute interpreter path,
no absolute ledger path, no assumption that ``~/.local/bin`` is on ``PATH``. Every
one of those was a real defect found by installing into a scratch project and running
the hook the way Cursor runs it.

The tests below install into a temporary project and *execute the installed hook*, not
the source — an install can copy every file correctly and still be broken because the
exec bit was stripped.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_SKILL = jrnl.SKILL_DIR
_INSTALL = _SKILL / "install.py"
_VERIFY = _SKILL / "hooks" / "verify_harnesses.py"
_PLUGIN = _SKILL / ".cursor-plugin" / "plugin.json"
_HOOK_TEMPLATE = _SKILL / "hooks" / "hooks-cursor.json"
_WRAPPER = _SKILL / "hooks" / "run-gate.sh"


def _load(name: str, path: Path):
    """Import a module from a file path, registering it for dataclass lookup."""
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


def _install_into(project: Path) -> subprocess.CompletedProcess[str]:
    """Run the skill's own installer for a project-scope Cursor install."""
    return subprocess.run(
        [sys.executable, str(_INSTALL), "--target", "cursor", "--project", str(project)],
        text=True,
        capture_output=True,
        check=False,
    )


def _run_hook(project: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    """Execute the *installed* hook wrapper from the project root, as Cursor does."""
    return subprocess.run(
        [str(project / ".cursor" / "skills" / "bug-fixer" / "hooks" / "run-gate.sh")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )


LEDGER = (
    "## Task\nfix it\n\n"
    "## Clarifications\nQ: a  A: b\nQ: c  A: d\nQ: e  A: f\n\n"
    "FAILING TEST: pytest -> 1 failed\nBASELINE: 5/5\n"
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A scratch project with a contract and a ledger, installed into."""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(
        "# contract\n\n## Ledger\n\n- Task/bug file: tasks/TASK-*.md\nTest paths: tests\n",
        encoding="utf-8",
    )
    (tasks / "TASK-100.md").write_text(LEDGER, encoding="utf-8")
    jrnl.append(tasks / "TASK-100.md", event="advance", **{"from": 1, "to": 1})
    result = _install_into(tmp_path)
    assert result.returncode == 0, result.stderr
    return tmp_path


# --- the install is portable ------------------------------------------------


def test_plugin_manifest_matches_the_documented_schema() -> None:
    """A Cursor plugin needs ``.cursor-plugin/plugin.json`` with a kebab-case name."""
    manifest = json.loads(_PLUGIN.read_text(encoding="utf-8"))
    assert manifest["name"] == "bug-fixer"
    assert manifest["version"].startswith("3.")
    assert manifest["hooks"].endswith(".json")
    # The root SKILL.md is the skill; a `skills` field would disable that fallback.
    assert "skills" not in manifest


def test_plugin_hook_uses_a_relative_path() -> None:
    """A plugin hook cannot embed an interpreter path that differs per machine."""
    hooks = json.loads(_HOOK_TEMPLATE.read_text(encoding="utf-8"))
    entry = hooks["hooks"]["preToolUse"][0]
    assert entry["command"].startswith("./hooks/")
    assert entry["failClosed"] is True
    assert "__PYTHON__" not in entry["command"]
    assert "__GATE__" not in entry["command"]


def test_wrapper_is_executable_in_the_source_tree() -> None:
    """The exec bit has to survive the copy, so it has to be set at the source."""
    mode = _WRAPPER.stat().st_mode
    assert mode & stat.S_IXUSR, "run-gate.sh is not executable in the repository"


def test_installer_writes_a_project_hook_per_harness(project: Path) -> None:
    """Each harness gets its own config file, all pointing at one wrapper.

    The wrapper — not the config — decides behaviour, so the harness difference lives
    entirely in the file shape.
    """
    rendered = {
        "cursor": project / ".cursor" / "hooks.json",
        "claude": project / ".claude" / "settings.json",
        "codex": project / ".codex" / "hooks.json",
    }
    for harness, path in rendered.items():
        subprocess.run(
            [sys.executable, str(_INSTALL), "--target", harness, "--project", str(project)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert path.is_file(), f"{harness} hook config was not written"
        body = path.read_text(encoding="utf-8")
        assert "bug-fixer/hooks/run-gate.sh" in body
        # No install-time absolute path: the harness supplies the project directory.
        assert str(project) not in body


def test_cursor_and_claude_templates_use_their_documented_project_variable() -> None:
    """A baked-in path breaks the moment the skill is installed elsewhere."""
    cursor = json.loads((_SKILL / "hooks" / "cursor.hooks.json").read_text(encoding="utf-8"))
    claude = json.loads((_SKILL / "hooks" / "claude.settings.json").read_text(encoding="utf-8"))
    codex = json.loads((_SKILL / "hooks" / "codex.hooks.json").read_text(encoding="utf-8"))

    assert "CURSOR_PROJECT_DIR" in cursor["hooks"]["preToolUse"][0]["command"]
    assert "CLAUDE_PROJECT_DIR" in claude["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # Codex documents no project variable, so the repo root is resolved at run time.
    assert "rev-parse --show-toplevel" in codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_installer_preserves_foreign_hooks(tmp_path: Path) -> None:
    """Replacing a project's hooks file would delete work this installer cannot see."""
    (tmp_path / ".cursor").mkdir()
    foreign = {
        "version": 1,
        "hooks": {
            "preToolUse": [{"command": "./hooks/mine.sh", "matcher": "Shell"}],
            "postToolUse": [{"command": "./hooks/audit.sh"}],
        },
    }
    (tmp_path / ".cursor" / "hooks.json").write_text(json.dumps(foreign), encoding="utf-8")

    assert _install_into(tmp_path).returncode == 0

    merged = json.loads((tmp_path / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert merged["hooks"]["postToolUse"] == foreign["hooks"]["postToolUse"]
    commands = [item["command"] for item in merged["hooks"]["preToolUse"]]
    assert "./hooks/mine.sh" in commands
    assert any("run-gate.sh" in command for command in commands)


def test_installer_is_idempotent_for_the_hook(project: Path) -> None:
    """Running the installer twice must not stack duplicate hook entries."""
    assert _install_into(project).returncode == 0
    hooks = json.loads((project / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    commands = [item["command"] for item in hooks["hooks"]["preToolUse"]]
    assert sum(1 for command in commands if "run-gate.sh" in command) == 1


def test_installed_wrapper_keeps_its_exec_bit(project: Path) -> None:
    """A hook that cannot execute is a fail-closed block on every tool call."""
    wrapper = project / ".cursor" / "skills" / "bug-fixer" / "hooks" / "run-gate.sh"
    mode = wrapper.stat().st_mode
    assert mode & stat.S_IXUSR, "install stripped +x from the hook wrapper"
    assert not mode & stat.S_IXOTH, "world may not execute it (permission policy §5.9)"


def test_no_absolute_paths_are_written_by_the_installer(project: Path) -> None:
    """Nothing machine-specific may be baked into the installed tree."""
    for relative in (".cursor/hooks.json",):
        text = (project / relative).read_text(encoding="utf-8")
        assert str(Path.home()) not in text, f"{relative} bakes a home path"
        assert str(project) not in text, f"{relative} bakes the install path"


# --- the installed hook actually runs ---------------------------------------


def test_installed_hook_denies_a_production_write(project: Path) -> None:
    """The end-to-end claim: install, run as the harness runs it, get a deny."""
    result = _run_hook(
        project,
        {
            "hook_event_name": "preToolUse",
            "cwd": str(project),
            "tool_name": "Write",
            "tool_input": {"file_path": "src/app.py"},
        },
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["permission"] == "deny"


def test_installed_hook_allows_a_test_write(project: Path) -> None:
    """The negative control: a gate that denies everything is not a gate."""
    result = _run_hook(
        project,
        {
            "hook_event_name": "preToolUse",
            "cwd": str(project),
            "tool_name": "Write",
            "tool_input": {"file_path": "tests/test_app.py"},
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["permission"] == "allow"


# --- ledger discovery without an embedded path ------------------------------


def test_gate_discovers_the_ledger_from_the_contract(project: Path) -> None:
    """No ``__LEDGER__`` placeholder: the contract names the pattern instead."""
    gate = _load("bugfixer_gate_portable", _SKILL / "hooks" / "bugfixer_gate.py")
    found = gate.discover_ledger(project)
    assert found is not None
    assert found.name == "TASK-100.md"


def test_gate_returns_nothing_when_the_contract_is_absent(tmp_path: Path) -> None:
    """No contract means no run this gate can prove — inert, not guessing."""
    gate = _load("bugfixer_gate_portable2", _SKILL / "hooks" / "bugfixer_gate.py")
    assert gate.discover_ledger(tmp_path) is None


def test_gate_returns_nothing_on_an_ambiguous_match(tmp_path: Path) -> None:
    """Two equally new ledgers would attach the run to an arbitrary bug."""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(
        "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n", encoding="utf-8"
    )
    for name in ("TASK-1.md", "TASK-2.md"):
        path = tasks / name
        path.write_text("## Task\nx\n", encoding="utf-8")
        os.utime(path, (1_700_000_000, 1_700_000_000))

    gate = _load("bugfixer_gate_portable3", _SKILL / "hooks" / "bugfixer_gate.py")
    assert gate.discover_ledger(tmp_path) is None


def test_gate_prefers_the_newest_ledger(tmp_path: Path) -> None:
    """The bug being worked on is the most recently touched task file."""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(
        "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n", encoding="utf-8"
    )
    old = tasks / "TASK-1.md"
    new = tasks / "TASK-2.md"
    old.write_text("## Task\nold\n", encoding="utf-8")
    new.write_text("## Task\nnew\n", encoding="utf-8")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))

    gate = _load("bugfixer_gate_portable4", _SKILL / "hooks" / "bugfixer_gate.py")
    found = gate.discover_ledger(tmp_path)
    assert found is not None and found.name == "TASK-2.md"


# --- the verifier tells the truth ------------------------------------------


def test_verifier_reports_a_binary_outside_path() -> None:
    """``shutil.which`` alone called ``cursor-agent`` missing while it was installed."""
    module = _load("verify_harnesses_test", _VERIFY)
    fake = module._OFF_PATH_DIRS[0] / "definitely-not-on-path-tool"
    if not module._OFF_PATH_DIRS[0].is_dir():
        pytest.skip("no off-PATH install directory on this machine")
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o700)
    try:
        row = module._classify("cursor", "definitely-not-on-path-tool", fake.with_suffix(".json"))
        assert row.verdict == "found-outside-path"
        assert row.installed is True
    finally:
        fake.unlink()


def test_verifier_never_reports_an_unobserved_pass() -> None:
    """A missing harness must not read as verified."""
    result = subprocess.run(
        [sys.executable, str(_VERIFY)], text=True, capture_output=True, check=False
    )
    assert "verified denies: 0 of 5" in result.stdout or "verified denies: 1 of 5" in result.stdout
    assert "NOT a pass" in result.stdout
