"""Session-start status, adapters, and the project rule (spec §18, invariants 40–42).

Three claims, each measured here rather than asserted in prose:

* **One answer, five spellings.** The core decides *what to say*; an adapter only lays it out.
  Each harness's response is checked separately, because the formats are vendor contracts and a
  shared "our format" would be wrong four times out of five.
* **Silence when ready.** A hook that always speaks is a hook that gets ignored, and the spec's
  invariant 41 says a ready project produces nothing.
* **The rule is merged, not imposed.** `AGENTS.md` belongs to the project; the plugin adds a marked
  block, a re-install does not stack a second one, and removal takes exactly its own block.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_SKILL = jrnl.SKILL_DIR
_HOOKS = jrnl.HOOKS
_ENTRY = _HOOKS / "session-start"
_SCRIPT = _HOOKS / "session_start.py"
_INSTALL = _SKILL / "install.py"

VERIFIED_CONTRACT = (
    "# contract\n\n"
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: make test\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)


def _load_adapters():
    """Import the adapter package and its five harness modules.

    Submodules are imported by name rather than through the package: `adapters` deliberately does
    not import them eagerly, so a harness's file is loaded only when that harness runs. A package
    that imported all five would pay for four formats it is not using.

    Returns:
        The ``adapters`` package, with each harness module reachable as an attribute.
    """
    if str(_HOOKS) not in sys.path:
        sys.path.insert(0, str(_HOOKS))
    package = importlib.import_module("adapters")
    for name in package.HARNESSES:
        setattr(package, name, importlib.import_module(f"adapters.{name}"))
    return package


def _import_package_module(name: str) -> object:
    """Import a module from ``lib/bugfixer`` as part of its package.

    The modules there use relative imports (one schema, one readiness core), so they have to be
    imported as ``bugfixer.<name>`` rather than loaded from a path — loading a file standalone
    would fail on the first relative import, and a test that worked around that would not be
    testing the module the plugin ships.

    Args:
        name: Module name inside the package, e.g. ``readiness``.

    Returns:
        The imported module.
    """
    lib = str(_SKILL / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    return importlib.import_module(f"bugfixer.{name}")


@pytest.fixture()
def readiness():
    """The shared readiness core."""
    return _import_package_module("readiness")


@pytest.fixture()
def rules():
    """The project-rule module."""
    return _import_package_module("rules")


@pytest.fixture()
def ready_project(tmp_path: Path) -> Path:
    """A project whose contract is complete, verified and has a ledger."""
    (tmp_path / "bug-fixer.project.md").write_text(VERIFIED_CONTRACT, encoding="utf-8")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "TASK-1.md").write_text("## Task\nfix\n", encoding="utf-8")
    return tmp_path


def _run_entry(harness: str, project: Path, payload: dict | None = None) -> subprocess.CompletedProcess[str]:
    """Run the shell entry the way a harness runs a hook.

    Args:
        harness: Harness id passed as the first argument.
        project: Working directory, i.e. the project the harness is talking about.
        payload: Event JSON for stdin; empty when omitted.

    Returns:
        The completed process.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CURSOR_", "CLAUDE_"))}
    return subprocess.run(
        [str(_ENTRY), harness],
        input=json.dumps(payload or {}),
        text=True,
        capture_output=True,
        cwd=str(project),
        env=env,
        check=False,
    )


# --- the core: one answer, three states ------------------------------------


def test_a_ready_project_produces_no_message(readiness, ready_project: Path) -> None:
    """Invariant 41: the hook stays silent when there is nothing to say."""
    state = readiness.inspect(ready_project)
    assert state.state == readiness.READY, state.problems
    assert readiness.message(state) == ""


def test_no_contract_is_named_as_the_reason(readiness, tmp_path: Path) -> None:
    """§18.4 names "no contract" as one of the three things worth saying."""
    message = readiness.message(readiness.inspect(tmp_path))
    assert "bug-fixer.project.md" in message
    assert "bugfixer doctor" in message


def test_an_unverified_contract_is_a_different_reason_from_a_missing_one(
    readiness, ready_project: Path
) -> None:
    """Two causes, two messages: a shared "something is wrong" sends the agent to the wrong fix."""
    contract = ready_project / "bug-fixer.project.md"
    contract.write_text(
        VERIFIED_CONTRACT.replace("agent 2026-11-27 — pytest ran", "not yet"), encoding="utf-8"
    )
    state = readiness.inspect(ready_project)
    assert state.state == readiness.UNVERIFIED
    message = readiness.message(state)
    assert "не подтверждён" in message
    assert "references/init.md" in message


def test_a_project_without_a_suite_is_named(readiness, ready_project: Path) -> None:
    """§6 point 2 makes the suite mandatory; silence about it would hide a blocked run."""
    contract = ready_project / "bug-fixer.project.md"
    contract.write_text(
        VERIFIED_CONTRACT.replace("Full suite: make test", "Full suite: none"), encoding="utf-8"
    )
    message = readiness.message(readiness.inspect(ready_project))
    assert "набора тестов нет" in message


def test_a_contract_with_no_ledger_file_is_named(readiness, ready_project: Path) -> None:
    """The gate refuses here too, so the status says so rather than reporting a ready project."""
    (ready_project / "tasks" / "TASK-1.md").unlink()
    state = readiness.inspect(ready_project)
    assert state.state == readiness.NO_LEDGER
    assert "леджер" in readiness.message(state)


def test_doctor_and_the_hook_share_one_implementation(readiness, ready_project: Path) -> None:
    """Two answers to "is this ready" is how the agent is told yes and then refused.

    The test compares the CLI's own report with the core's: if `doctor` grew a second check, this
    is where the two would diverge.
    """
    state = readiness.inspect(ready_project)
    result = subprocess.run(
        [sys.executable, str(_SKILL / "bin" / "bugfixer"), "doctor", "--project", str(ready_project)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(state.ledger) in result.stdout


# --- the adapters: one key each, in the harness's own spelling --------------


def test_cursor_answers_with_additional_context() -> None:
    """Cursor's `sessionStart` field. Any second key would be a second, unknown instruction."""
    adapters = _load_adapters()
    payload = adapters.cursor.render_context("not ready")
    assert payload == {"additional_context": "not ready"}


def test_claude_nests_the_context_and_names_the_event() -> None:
    """Claude Code reads both spellings without de-duplicating, so there must be one."""
    adapters = _load_adapters()
    payload = adapters.claude.render_context("not ready")
    assert list(payload) == ["hookSpecificOutput"]
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert inner["additionalContext"] == "not ready"


def test_codex_takes_the_flat_field() -> None:
    """Codex reads `additionalContext` at the top level; nesting it would silently do nothing."""
    adapters = _load_adapters()
    assert adapters.codex.render_context("not ready") == {"additionalContext": "not ready"}


def test_openclaw_blocks_with_a_non_empty_reason() -> None:
    """A block with no reason is the broken guard §0 names."""
    adapters = _load_adapters()
    payload = adapters.openclaw.render_context("not ready")
    assert payload["block"] is True
    assert payload["blockReason"] == "not ready"


def test_openclaw_refuses_to_block_without_a_reason() -> None:
    """The caller must stay silent instead; an empty blockReason is worse than no block."""
    adapters = _load_adapters()
    with pytest.raises(ValueError):
        adapters.openclaw.render_context("   ")


def test_opencode_refuses_rather_than_pretending() -> None:
    """It has no context channel, so `{}` would read as "ready" — the one wrong answer."""
    adapters = _load_adapters()
    with pytest.raises(adapters.UnsupportedSessionContext) as excinfo:
        adapters.opencode.render_context("not ready")
    assert "AGENTS.md" in str(excinfo.value), "the refusal does not name the channel that works"


def test_every_harness_has_an_adapter_file() -> None:
    """Invariant 40: a file per harness, so a harness-specific defect has one place to live."""
    adapters = _load_adapters()
    files = {path.stem for path in (_HOOKS / "adapters").glob("*.py") if path.stem != "__init__"}
    assert files == set(adapters.HARNESSES), f"adapter files {sorted(files)} != {adapters.HARNESSES}"


def test_a_two_key_response_is_refused_by_the_guard() -> None:
    """The guard exists because the failure it prevents is invisible: double-injected context."""
    adapters = _load_adapters()
    with pytest.raises(AssertionError):
        adapters.one_key({"a": 1, "b": 2})


# --- the entry point: silence, one payload, or a wiring error ----------------


def test_the_entry_stays_silent_for_a_ready_project(ready_project: Path) -> None:
    """A hook that always speaks costs context in every session and gets ignored."""
    result = _run_entry("cursor", ready_project)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_the_entry_emits_cursor_shape_in_a_project_without_a_contract(tmp_path: Path) -> None:
    """The end-to-end claim for the harness we can run: shell entry, adapter, JSON, one key."""
    result = _run_entry("cursor", tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert list(payload) == ["additional_context"]
    assert "bug-fixer.project.md" in payload["additional_context"]


@pytest.mark.parametrize(
    ("harness", "key"),
    [
        ("cursor", "additional_context"),
        ("claude", "hookSpecificOutput"),
        ("codex", "additionalContext"),
        ("openclaw", "block"),
    ],
)
def test_each_harness_gets_its_own_shape_from_the_entry(
    harness: str, key: str, tmp_path: Path
) -> None:
    """One run per harness, because the format is the harness's, not ours."""
    result = _run_entry(harness, tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert key in payload, payload
    assert len(payload) == 1 if harness != "openclaw" else len(payload) == 2


def test_opencode_says_nothing_and_explains_why(tmp_path: Path) -> None:
    """No channel means no payload; the reason goes to stderr, where a person reads it."""
    result = _run_entry("opencode", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "AGENTS.md" in result.stderr


def test_an_unknown_harness_is_a_wiring_error(tmp_path: Path) -> None:
    """A guessed default would be a silent mis-spelling of somebody else's format."""
    result = _run_entry("gemini", tmp_path)
    assert result.returncode == 2
    assert "cursor, claude, codex, opencode, openclaw" in result.stderr


def test_the_entry_names_no_interpreter_path() -> None:
    """A hook cannot embed an interpreter path that differs per machine."""
    text = _ENTRY.read_text(encoding="utf-8")
    assert "python3" in text
    assert str(Path.home()) not in text
    assert "/usr/bin/env python" not in text


def test_the_entry_contains_no_inline_python() -> None:
    """§5.4: bash orchestrates, Python implements. An inline block is the pattern it forbids."""
    lines = _ENTRY.read_text(encoding="utf-8").splitlines()
    code = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    assert not any("<<" in line for line in code), "heredoc in a shell entry point"
    assert not any(line.strip().startswith(("import ", "from ")) for line in code)
    assert not any("python3 -c" in line for line in code)


# --- the project rule: written, merged, removed ------------------------------


def test_created_files_carry_no_world_access(rules, tmp_path: Path) -> None:
    """§5.9: the plugin must not grant other users access to what it writes.

    Measured before the fix: `.cursor/rules/bug-fixer.mdc` and a generated `AGENTS.md` landed
    `0644` (world-readable) because `write_text` inherits the process umask. Directories the plugin
    creates are clamped too — `0770`, never `0755`.
    """
    rules.write_owned(tmp_path / rules.CURSOR_RULE_FILE, rules.cursor_rule_text())
    rules.merge(tmp_path / rules.AGENTS_FILE, rules.block())
    for path in (tmp_path / rules.CURSOR_RULE_FILE, tmp_path / rules.AGENTS_FILE):
        mode = path.stat().st_mode & 0o777
        assert not mode & 0o007, f"{path.name} is world-accessible ({oct(mode)})"
    directory = (tmp_path / rules.CURSOR_RULE_FILE).parent
    assert not directory.stat().st_mode & 0o007, "a created directory is world-accessible"


def test_a_projects_own_file_mode_is_left_alone(rules, tmp_path: Path) -> None:
    """Merging into `AGENTS.md` must not silently re-permission a file the project owns."""
    path = tmp_path / rules.AGENTS_FILE
    path.write_text("# Project\n", encoding="utf-8")
    path.chmod(0o644)
    rules.merge(path, rules.block())
    assert path.stat().st_mode & 0o777 == 0o644, "the plugin narrowed a project-owned file"


def test_the_generated_contract_carries_no_world_access(tmp_path: Path) -> None:
    """Same policy for the contract `init` writes, and for a `.gitignore` the plugin creates."""
    result = subprocess.run(
        [sys.executable, str(_SKILL / "bin" / "bugfixer"), "init", "--project", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in ("bug-fixer.project.md", ".gitignore"):
        mode = (tmp_path / name).stat().st_mode & 0o777
        assert not mode & 0o007, f"{name} is world-accessible ({oct(mode)})"


def test_the_mode_constants_are_declared_in_one_place() -> None:
    """`perms` owns the policy; `contract.py` repeats the literal because it must stay standalone.

    `contract.py` is loaded from its path by `bin/bugfixer` and by several tests, so it cannot use a
    relative import. The repetition is therefore deliberate and this test is what keeps it honest:
    change the policy in one place and the other fails.
    """
    perms = _import_package_module("perms")
    contract = _import_package_module("contract")
    assert contract._CREATED_FILE_MODE == perms.FILE_MODE


def test_an_existing_gitignore_keeps_its_mode(tmp_path: Path) -> None:
    """The project's own file: the plugin appends a rule, it does not re-permission the file."""
    path = tmp_path / ".gitignore"
    path.write_text("node_modules/\n", encoding="utf-8")
    path.chmod(0o644)
    result = subprocess.run(
        [sys.executable, str(_SKILL / "bin" / "bugfixer"), "init", "--project", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "*.bugfixer.jsonl" in path.read_text(encoding="utf-8")
    assert path.stat().st_mode & 0o777 == 0o644, "the plugin narrowed a project-owned file"


def test_cursor_rule_file_is_written_with_always_apply(rules, tmp_path: Path) -> None:
    """Without `alwaysApply` the rule is a file nobody reads that looks installed."""
    merged = rules.write_owned(tmp_path / rules.CURSOR_RULE_FILE, rules.cursor_rule_text())
    assert merged.action == "written"
    text = (tmp_path / rules.CURSOR_RULE_FILE).read_text(encoding="utf-8")
    assert "alwaysApply: true" in text
    assert "bugfixer doctor" in text


def test_agents_block_is_appended_to_an_existing_file(rules, tmp_path: Path) -> None:
    """`AGENTS.md` belongs to the project; its own text must survive untouched."""
    path = tmp_path / rules.AGENTS_FILE
    path.write_text("# This project\n\nOur own rules.\n", encoding="utf-8")
    rules.merge(path, rules.block())
    text = path.read_text(encoding="utf-8")
    assert "Our own rules." in text
    assert "bugfixer doctor" in text
    assert text.count(rules.BEGIN) == 1


def test_reinstalling_the_rule_does_not_duplicate_it(rules, tmp_path: Path) -> None:
    """Two copies drift, and the copy the agent trusts is the stale one."""
    path = tmp_path / rules.AGENTS_FILE
    rules.merge(path, rules.block())
    second = rules.merge(path, rules.block())
    assert second.action == "unchanged"
    assert path.read_text(encoding="utf-8").count(rules.BEGIN) == 1


def test_an_updated_rule_replaces_only_its_own_block(rules, tmp_path: Path) -> None:
    """The plugin's text changes between versions; the project's text never does."""
    path = tmp_path / rules.AGENTS_FILE
    path.write_text(f"# Project\n\n{rules.BEGIN}\nold text\n{rules.END}\n\n## After\n", encoding="utf-8")
    rules.merge(path, rules.block())
    text = path.read_text(encoding="utf-8")
    assert "old text" not in text
    assert "# Project" in text and "## After" in text


def test_removal_takes_the_block_and_leaves_the_project_file(rules, tmp_path: Path) -> None:
    """Uninstall must not delete a file the project wrote before the plugin arrived."""
    path = tmp_path / rules.AGENTS_FILE
    path.write_text("# Project\n\nour own rules\n", encoding="utf-8")
    rules.merge(path, rules.block())
    rules.strip(path)
    text = path.read_text(encoding="utf-8")
    assert rules.BEGIN not in text
    assert "our own rules" in text


def test_removal_deletes_a_file_that_held_only_the_block(rules, tmp_path: Path) -> None:
    """An empty `AGENTS.md` the project never had would look like a project decision."""
    path = tmp_path / rules.AGENTS_FILE
    rules.merge(path, rules.block())
    rules.strip(path)
    assert not path.exists()


def test_claude_import_is_added_once(rules, tmp_path: Path) -> None:
    """Claude Code reads `CLAUDE.md`; `@AGENTS.md` is the import, so the text has one home."""
    path = tmp_path / rules.CLAUDE_FILE
    assert rules.ensure_claude_import(path).action == "written"
    assert rules.ensure_claude_import(path).action == "kept"
    assert path.read_text(encoding="utf-8").count(rules.CLAUDE_IMPORT) == 1


def test_the_rule_names_no_project_specific_value(rules) -> None:
    """A rule that repeated the test command would be a second copy, and the stale one."""
    body = "\n".join(rules.BODY)
    for leaked in ("make test", "pytest", "tasks/TASK-", "tests/"):
        assert leaked not in body, f"the rule bakes in {leaked!r}, which lives in the contract"


# --- the installer writes and removes the rule ------------------------------


def _install(project: Path, target: str = "cursor", *extra: str) -> subprocess.CompletedProcess[str]:
    """Run the plugin's installer against a scratch project."""
    return subprocess.run(
        [sys.executable, str(_INSTALL), "--target", target, "--project", str(project), *extra],
        text=True,
        capture_output=True,
        check=False,
    )


def test_install_writes_the_cursor_rule(ready_project: Path) -> None:
    """The main initialisation channel has to arrive with the install, not by hand."""
    assert _install(ready_project).returncode == 0
    rule = ready_project / ".cursor" / "rules" / "bug-fixer.mdc"
    assert rule.is_file()
    assert "alwaysApply: true" in rule.read_text(encoding="utf-8")


def test_install_writes_agents_md_for_codex(ready_project: Path) -> None:
    """Codex reads `AGENTS.md` from the repository root; there is no other channel for it."""
    assert _install(ready_project, "codex").returncode == 0
    agents = ready_project / "AGENTS.md"
    assert agents.is_file()
    assert "bugfixer doctor" in agents.read_text(encoding="utf-8")


def test_install_gives_claude_the_import_not_a_second_copy(ready_project: Path) -> None:
    """Two copies of the instruction would disagree the first time one is edited."""
    assert _install(ready_project, "claude").returncode == 0
    agents = (ready_project / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ready_project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in claude
    assert "bugfixer doctor" in agents
    assert "bugfixer doctor" not in claude, "the instruction was copied instead of imported"


def test_uninstall_removes_the_rule(ready_project: Path) -> None:
    """A rule left behind tells the agent to run a command whose plugin is gone."""
    assert _install(ready_project).returncode == 0
    rule = ready_project / ".cursor" / "rules" / "bug-fixer.mdc"
    assert rule.is_file()
    assert _install(ready_project, "cursor", "--uninstall").returncode == 0
    assert not rule.is_file(), "the rule outlived its plugin"


def test_the_rule_mapping_exists_in_one_place(rules, tmp_path: Path) -> None:
    """`install.py` and `init` must place the same files for the same harness.

    Two copies of "which file does this harness read" would drift, and the drift would show as a
    harness that installs cleanly and has no rule — the main channel silently missing.
    """
    assert rules.HARNESSES == ("cursor", "claude", "codex", "opencode", "openclaw")
    for harness in rules.HARNESSES:
        placed = rules.for_harness(tmp_path, harness, dry_run=True)
        assert placed, f"{harness} places no rule at all"
    cursor = {merged.path for merged in rules.for_harness(tmp_path, "cursor", dry_run=True)}
    assert cursor == {tmp_path / rules.CURSOR_RULE_FILE}
    codex = {merged.path for merged in rules.for_harness(tmp_path, "codex", dry_run=True)}
    assert codex == {tmp_path / rules.AGENTS_FILE}


def test_every_install_and_removal_names_the_restart(tmp_path: Path) -> None:
    """DoD-8: a change to skills, rules or hooks is invisible until the harness re-reads them.

    Without the line the install looks broken — the skill is not in the `/` list, the rule does not
    apply, the hook never fires — and the operator's reasonable conclusion is that the plugin
    failed. Cursor is named with its own remedy: an open window must be reloaded, because a new
    chat does not re-read skill discovery.
    """
    for action in ("--uninstall", ""):
        args = [str(_INSTALL), "--target", "cursor", "--project", str(tmp_path)]
        if action:
            args.append(action)
        result = subprocess.run(
            [sys.executable, *args], text=True, capture_output=True, check=False
        )
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert "Reload Window" in combined, combined
        assert "Cursor" in combined, combined


def _load_installer():
    """Import the plugin's installer from its path, registered for dataclass lookup.

    The installer is a script, not a package module, so it is loaded by path — the same way the
    plugin's own `bin/bugfixer` reaches its library. Registering it in `sys.modules` before
    execution is what lets its dataclasses resolve their string annotations.
    """
    name = "install_plugin_targets"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _INSTALL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def test_the_restart_notice_covers_every_installed_harness(tmp_path: Path) -> None:
    """A harness with no advice would be the one where "it does not work" happens."""
    module = _load_installer()
    lines = module.reload_notice(list(module._PROJECT_TARGETS), action="installed")
    assert len(lines) >= 2
    for target in module._PROJECT_TARGETS:
        assert module.RELOAD_ADVICE.get(target), f"no restart advice for {target}"


def test_the_restart_notice_is_not_printed_for_an_unknown_target() -> None:
    """Silence beats invented advice: `openclaw` needs no window, and guessing would be wrong."""
    module = _load_installer()
    assert module.reload_notice([], action="installed") == []
    assert module.reload_notice(["nonexistent"], action="installed") == []


def test_uninstall_keeps_the_project_agents_md_text(ready_project: Path) -> None:
    """Removing the plugin is not a reason to lose the project's own instructions."""
    (ready_project / "AGENTS.md").write_text("# Project\n\nours\n", encoding="utf-8")
    assert _install(ready_project, "codex").returncode == 0
    assert _install(ready_project, "codex", "--uninstall").returncode == 0
    text = (ready_project / "AGENTS.md").read_text(encoding="utf-8")
    assert "ours" in text
    assert "bugfixer doctor" not in text


def test_uninstall_removes_every_hook_entry_not_just_the_gate(ready_project: Path) -> None:
    """Measured defect: the marker named `run-gate.sh`, so `sessionStart` survived removal.

    The left-over entry pointed at a deleted script. For `preToolUse` with `failClosed` that blocks
    every tool call; for the status hook it is a command that cannot run. Either way the project
    keeps a broken wiring after the plugin is gone, which is what removal exists to prevent.
    """
    assert _install(ready_project).returncode == 0
    hooks = ready_project / ".cursor" / "hooks.json"
    assert "session-start" in hooks.read_text(encoding="utf-8")
    assert _install(ready_project, "cursor", "--uninstall").returncode == 0
    assert not hooks.is_file(), "the hook config outlived the plugin, pointing at deleted scripts"


def test_init_places_the_rule_so_a_plugin_only_install_is_initialised(tmp_path: Path) -> None:
    """A plugin installed from a marketplace never runs `install.py`, so `init` must place it.

    Without this, the main initialisation channel (§18.3) would only exist for installs that used
    the installer — the plugin's own route would leave the project with a gate and no instruction.
    """
    result = subprocess.run(
        [sys.executable, str(_SKILL / "bin" / "bugfixer"), "init", "--project", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rule = tmp_path / ".cursor" / "rules" / "bug-fixer.mdc"
    assert rule.is_file(), "`init` did not place the Cursor rule"
    assert "alwaysApply: true" in rule.read_text(encoding="utf-8")
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file(), "`init` did not place the shared AGENTS.md block"
    assert (tmp_path / "CLAUDE.md").is_file()


def test_init_twice_does_not_duplicate_the_rule(tmp_path: Path) -> None:
    """The contract is written once, but the rule must survive a forced re-init unchanged."""
    cli = [sys.executable, str(_SKILL / "bin" / "bugfixer"), "init", "--project", str(tmp_path)]
    assert subprocess.run(cli, text=True, capture_output=True, check=False).returncode == 0
    forced = [*cli, "--force"]
    assert subprocess.run(forced, text=True, capture_output=True, check=False).returncode == 0
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("bug-fixer:project-rule:begin") == 1
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").count("@AGENTS.md") == 1


def test_installing_twice_does_not_duplicate_the_status_hook(ready_project: Path) -> None:
    """One entry per event: two session hooks would both speak, and the second is noise."""
    assert _install(ready_project).returncode == 0
    assert _install(ready_project).returncode == 0
    hooks = json.loads((ready_project / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    commands = [item["command"] for item in hooks["hooks"]["sessionStart"]]
    assert sum(1 for command in commands if "session-start" in command) == 1


def test_foreign_hooks_survive_both_the_install_and_the_removal(ready_project: Path) -> None:
    """The installer owns its own entries and nothing else, in either direction."""
    hooks = ready_project / ".cursor" / "hooks.json"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text(
        json.dumps(
            {"version": 1, "hooks": {"sessionStart": [{"command": "./hooks/mine.sh"}], "stop": [{"command": "./hooks/bye.sh"}]}}
        ),
        encoding="utf-8",
    )
    assert _install(ready_project).returncode == 0
    assert _install(ready_project, "cursor", "--uninstall").returncode == 0
    merged = json.loads(hooks.read_text(encoding="utf-8"))
    assert merged["hooks"]["sessionStart"] == [{"command": "./hooks/mine.sh"}]
    assert merged["hooks"]["stop"] == [{"command": "./hooks/bye.sh"}]


def test_the_adapter_is_chosen_by_the_harness_not_by_the_environment(tmp_path: Path) -> None:
    """Invariant 40: sniffing supplies parameters, it never picks the adapter.

    The environment here claims Cursor while the harness argument says codex. Getting Codex's
    field is the correct answer: a dispatcher that trusted the variable would emit Cursor's, and
    the harness would ignore it — a hook that is wired and silent.
    """
    env = {**os.environ, "CURSOR_PROJECT_DIR": str(tmp_path), "CLAUDE_PROJECT_DIR": str(tmp_path)}
    result = subprocess.run(
        [str(_ENTRY), "codex"],
        input="{}",
        text=True,
        capture_output=True,
        cwd=str(tmp_path),
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert list(payload) == ["additionalContext"], payload

