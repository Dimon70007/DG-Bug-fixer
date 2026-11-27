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
import re
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_SKILL = jrnl.SKILL_DIR
_CLI = _SKILL / "bin" / "bugfixer"
_GATE = _SKILL / "hooks" / "bugfixer_gate.py"

_WRITE = {"tool_name": "Write", "tool_input": {"file_path": "src/core.py"}}


def _run_cli(*args: str, cwd: Path | None = None, home: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the plugin CLI.

    Args:
        *args: CLI arguments.
        cwd: Working directory for the call.
        home: ``HOME`` to use. Tests pass a scratch directory so the user's real MCP settings —
            and their API keys — never influence a result, and so a machine with a search server
            configured cannot make a test pass that would fail elsewhere.

    Returns:
        The completed process.
    """
    env = None
    if home is not None:
        import os

        env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


@pytest.fixture()
def empty_home(tmp_path: Path) -> Path:
    """A HOME with no MCP configuration, so a test sees only what it creates.

    Without this the tests read the developer's ``~/.cursor/mcp.json``: a machine with a search
    server configured would make them pass, and one without would fail. Worse, they would be
    reading real API keys.
    """
    home = tmp_path / "home"
    home.mkdir()
    return home


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


def test_init_writes_a_contract_the_schema_recognises(tmp_path: Path, empty_home: Path) -> None:
    """The file must be at the path the skill looks for, and must parse."""
    result = _run_cli("init", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 0, result.stdout + result.stderr
    contract = tmp_path / "bug-fixer.project.md"
    assert contract.is_file(), "init did not create the contract at the documented path"
    assert "Task/bug file:" in contract.read_text(encoding="utf-8")


def test_init_fills_in_what_it_can_prove(tmp_path: Path, empty_home: Path) -> None:
    """A test directory and a task directory are facts on disk, not guesses.

    ``§0`` forbids guessing a command; it does not forbid reading the filesystem. Filling in
    what exists removes the two placeholders that cause the most silent breakage.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    result = _run_cli("init", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "Test paths: tests/" in text, text
    assert "Task/bug file: tasks/" in text, text


def test_init_leaves_unprovable_values_as_visible_placeholders(tmp_path: Path, empty_home: Path) -> None:
    """An empty project has nothing to infer from, so a gap that matters stays visible.

    `Full suite:` becomes `none` — no manifest and no test directory is a project without a
    suite, and saying so is a statement rather than a placeholder. `Task/bug file:` stays a
    placeholder because there is nowhere to record findings until one exists, and inventing a
    directory would create a ledger the project does not want.
    """
    result = _run_cli("init", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "Task/bug file: <" in text, "a ledger directory was invented in an empty project"
    assert "Full suite: none" in text


# --- out of the box: sensible defaults, still editable ----------------------


def test_init_fills_search_with_the_tools_cursor_actually_provides(tmp_path: Path, empty_home: Path) -> None:
    """Search comes from the MCP settings, not from a list baked into the plugin.

    The plugin cannot know which search servers a machine has: one project runs Tavily, another
    runs a self-hosted index, a third has none. Reading the MCP configuration is the only way to
    name tools the agent can actually call, and a name that does not exist is worse than none —
    the agent would follow step 3.3 with a tool it does not have.
    """
    mcp = tmp_path / ".cursor" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(
        json.dumps({"mcpServers": {"my-tavily": {"command": "x"}, "local-index": {"command": "y"}}}),
        encoding="utf-8",
    )
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "my-tavily" in text, text
    assert "local-index" in text, text
    assert "<comma-separated tools" not in text, "Search is still a placeholder"


def test_init_never_copies_mcp_secrets_into_the_contract(tmp_path: Path, empty_home: Path) -> None:
    """An MCP config holds API keys; the contract is a file people commit.

    Only server *names* are read. Args and env of a server entry are the one place a token is
    guaranteed to be, and a contract that leaked one would put it in git.
    """
    mcp = tmp_path / ".cursor" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "search-x": {
                        "command": "npx",
                        "args": ["-y", "some-mcp"],
                        "env": {"SEARCH_API_KEY": "sk-live-DEADBEEF", "TOKEN": "hunter2"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "search-x" in text
    assert "sk-live-DEADBEEF" not in text, "an MCP secret reached the contract"
    assert "hunter2" not in text
    assert "SEARCH_API_KEY" not in text


def test_init_keeps_the_builtin_search_when_mcp_lists_one(tmp_path: Path, empty_home: Path) -> None:
    """The editor's own search is a tool the agent has even with no MCP server configured."""
    mcp = tmp_path / ".cursor" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(json.dumps({"mcpServers": {"tavily": {"command": "x"}}}), encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "tavily" in text
    assert "WebSearch" in text


def test_init_says_so_when_no_search_server_is_configured(tmp_path: Path, empty_home: Path) -> None:
    """No MCP config means no server names to name; the field must not invent any."""
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "WebSearch" in text, "the builtin search tool is always available"
    assert "tavily" not in text.lower(), "a search server was named that this machine does not have"


def test_init_reports_which_fields_the_agent_must_confirm(tmp_path: Path, empty_home: Path) -> None:
    """`init` fills what a filesystem can prove; the agent confirms the rest against its runtime.

    The plugin cannot see the agent's toolset, so the contract it writes is a starting point the
    agent verifies: the test command by running it, the search tools against what it actually
    has. `init` names those fields instead of implying everything is settled.
    """
    result = _run_cli("init", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "confirm" in combined.lower() or "verify" in combined.lower(), combined


# --- what init must not leave hard-coded ------------------------------------


@pytest.mark.parametrize(
    "probe",
    [
        "tests",
        "test",
        "__tests__",
        "pkg/tests",
        "spec",
        "src/__tests__",
    ],
)
def test_init_finds_the_test_directory_whatever_it_is_called(
    tmp_path: Path, empty_home: Path, probe: str
) -> None:
    """`tests/` is a convention, not a fact: plenty of projects use another name.

    The old code checked for the literal directory `tests` and gave up otherwise. A project with
    `spec/` or `__tests__/` got a placeholder, so the gate had no test paths and refused the
    test write Phase 1 exists to make.

    A directory whose name only *suggests* tests must also hold one: `spec/` was accepted for its
    name alone before 2026-11-27, and `docs/superpowers/specs/` then became a "test path" — prose
    declared to be tests. The probe therefore writes a test file inside, which is what makes the
    directory a test path rather than a word.
    """
    directory = tmp_path / probe
    directory.mkdir(parents=True)
    (directory / "test_sample.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert probe in text.split("Test paths:")[1].splitlines()[0], text


def test_a_directory_with_no_tests_is_not_a_test_path(tmp_path: Path, empty_home: Path) -> None:
    """Measured defect: `docs/superpowers/specs/` was accepted for its name alone.

    The contract then told the gate that prose was tests, which both opened a write path the
    phase ban exists to close and hid every real test from it.
    """
    (tmp_path / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "superpowers" / "specs" / "guide.md").write_text("# spec\n")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    line = text.split("Test paths:")[1].splitlines()[0]
    assert "specs" not in line, line


@pytest.mark.parametrize(
    "probe",
    ["tasks", "docs/bugs", "issues", "bugs", "spec/tasks"],
)
def test_init_finds_the_ledger_directory_whatever_it_is_called(
    tmp_path: Path, empty_home: Path, probe: str
) -> None:
    """The ledger location is a layout decision, not `tasks/` or nothing."""
    directory = tmp_path / probe
    directory.mkdir(parents=True)
    # A task file makes the directory a ledger rather than a namesake: `spec/tasks/` in a docs
    # tree and `tasks/` at the root are only distinguishable by what they hold.
    (directory / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    ledger = text.split("Task/bug file:")[1].splitlines()[0]
    assert probe in ledger, text


def test_init_detects_a_test_command_in_a_subdirectory(tmp_path: Path, empty_home: Path) -> None:
    """A monorepo keeps its manifest below the root; a root-only check finds nothing.

    The old code looked at ``project / "pyproject.toml"`` and nothing else, so a repository whose
    projects live in ``apps/api`` and ``apps/web`` got no command at all.
    """
    api = tmp_path / "apps" / "api"
    api.mkdir(parents=True)
    (api / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (api / "tests").mkdir()
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "pytest" in text.split("Full suite:")[1].splitlines()[0], text


def test_init_ignores_manifests_inside_vendor_directories(
    tmp_path: Path, empty_home: Path
) -> None:
    """`node_modules` is full of `package.json` files; none of them describe this project."""
    vendor = tmp_path / "node_modules" / "some-package"
    vendor.mkdir(parents=True)
    (vendor / "package.json").write_text('{"name": "dep"}\n', encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "npm test" not in text, "a dependency's manifest was read as this project's toolchain"


def test_init_writes_the_journal_rule_into_gitignore(tmp_path: Path, empty_home: Path) -> None:
    """Invariant 35 has to hold in the project the plugin is installed into, not only here.

    The journal is local history (§5.4) and must never be committed. Nothing enforced that in a
    target project: ``init`` wrote a contract and left ``.gitignore`` alone, so the first run
    staged `TASK-1.md.bugfixer.jsonl` like any other file.
    """
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file(), "init did not create a .gitignore"
    assert "*.bugfixer.jsonl" in gitignore.read_text(encoding="utf-8")


def test_init_appends_to_an_existing_gitignore_without_losing_it(
    tmp_path: Path, empty_home: Path
) -> None:
    """A project's ignore rules are its own; adding one must not replace the rest."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# mine\nnode_modules/\n*.log\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = gitignore.read_text(encoding="utf-8")
    assert "node_modules/" in text and "*.log" in text and "# mine" in text
    assert "*.bugfixer.jsonl" in text


def test_init_is_idempotent_about_gitignore(tmp_path: Path, empty_home: Path) -> None:
    """Running init twice must not stack duplicate rules."""
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    (tmp_path / "bug-fixer.project.md").unlink()
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.count("*.bugfixer.jsonl") == 1, text


def test_init_warns_when_the_ledger_pattern_matches_nothing(
    tmp_path: Path, empty_home: Path
) -> None:
    """A pattern with no file means the gate cannot start; saying so beats a silent placeholder.

    The placeholder is honest but vague — "path or glob" does not tell the operator that the gate
    will refuse work until a file exists. The warning names the consequence.
    """
    result = _run_cli("init", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "ledger" in combined.lower()
    assert "no run" in combined.lower() or "refuses" in combined.lower(), combined


# --- the contract says how much of it has been checked -----------------------


def test_generated_contract_states_it_is_unverified(tmp_path: Path, empty_home: Path) -> None:
    """`init` derives values from files; it cannot know they work.

    Without a marker the generated file looks final: `doctor` exits 0 and the gate starts
    enforcing a command nobody ran. The marker is what makes "derived" different from "true".
    """
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert "Contract verified by:" in text
    assert "not yet" in text.lower()
    assert "Contract version:" in text


def test_doctor_warns_that_an_unverified_contract_has_not_been_checked(
    tmp_path: Path, empty_home: Path
) -> None:
    """Exit 0 because the file is well-formed, a warning because nothing was confirmed."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0

    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "not been verified" in combined.lower() or "unverified" in combined.lower(), combined


def test_gate_refuses_work_until_the_contract_is_verified(
    tmp_path: Path, empty_home: Path
) -> None:
    """The operator's rule: configure once, then work.

    A generated contract is a proposal. Until someone confirmed the test command actually runs,
    phase 1 would record a baseline from a command that may not exist — wasting the first
    iteration on discovering that the contract lied.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0

    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2, f"an unverified contract let work start: {result.stdout}"
    message = json.loads(result.stdout)["agent_message"]
    assert "not been verified" in message.lower() or "unverified" in message.lower(), message
    assert "references/init.md" in message or "confirm" in message.lower(), message


def test_verifying_the_contract_opens_the_gate(tmp_path: Path, empty_home: Path) -> None:
    """The legal path must be one edit away, or the refusal is a lockout."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0

    contract = tmp_path / "bug-fixer.project.md"
    text = contract.read_text(encoding="utf-8").replace(
        "Contract verified by: not yet", "Contract verified by: agent 2026-11-27 — pytest ran, 12 passed"
    )
    contract.write_text(text, encoding="utf-8")

    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2
    assert "Phase 1" in json.loads(result.stdout)["agent_message"], (
        "the gate did not reach its real rules after verification"
    )


def test_writing_the_marker_itself_is_never_refused(tmp_path: Path, empty_home: Path) -> None:
    """Confirming the contract is setup: the gate must not block the edit that unblocks it."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0

    result = _run_gate(
        {"tool_name": "StrReplace", "tool_input": {"file_path": "bug-fixer.project.md"}},
        tmp_path,
    )
    assert result.returncode == 0, f"the gate refused the edit that confirms it: {result.stdout}"


def test_a_missing_marker_counts_as_unverified(tmp_path: Path, empty_home: Path) -> None:
    """A hand-written contract predates the marker; treating it as verified would be a guess."""
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
    message = json.loads(result.stdout)["agent_message"].lower()
    assert "verified" in message, message


# --- the schema has a version -----------------------------------------------


def test_doctor_warns_when_the_contract_is_older_than_the_schema(
    tmp_path: Path, empty_home: Path
) -> None:
    """A contract written for an older schema can pass the field checks and still be wrong.

    Without a version, `doctor` has no way to say "this file predates a field it should have";
    the operator would only find out when a rule behaved unexpectedly.
    """
    (tmp_path / "bug-fixer.project.md").write_text(
        "Contract version: 0\n"
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\nContract verified by: agent 2026-11-27 — confirmed\n"
        "Allowed: none\n\n"
        "## Extra prohibitions\n\n- no sudo\n",
        encoding="utf-8",
    )
    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    combined = result.stdout + result.stderr
    assert "version" in combined.lower(), combined
    assert "bugfixer init" in combined, "the warning does not say how to regenerate"


def test_doctor_warns_when_the_contract_is_newer_than_the_schema(
    tmp_path: Path, empty_home: Path
) -> None:
    """A newer contract may rely on fields this plugin does not know; that is worth saying."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Contract version: 99\n"
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\nContract verified by: agent 2026-11-27 — confirmed\n"
        "Allowed: none\n\n"
        "## Extra prohibitions\n\n- no sudo\n",
        encoding="utf-8",
    )
    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    combined = result.stdout + result.stderr
    assert "version" in combined.lower(), combined


# --- a project with no tests is stopped, with options -----------------------


def _no_suite_contract(tmp_path: Path) -> None:
    """Write a verified contract that declares there is no test suite."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Contract version: 1\n"
        "Task/bug file: tasks/TASK-*.md\nFull suite: none\nTest paths: tests/\n"
        "Allowed: none\nContract verified by: agent 2026-11-27\n\n"
        "## Extra prohibitions\n\n- no sudo\n",
        encoding="utf-8",
    )


def test_no_suite_is_a_problem_not_a_warning(tmp_path: Path, empty_home: Path) -> None:
    """§6 point 2 makes the full-suite command mandatory; §1 needs a baseline to exist.

    A run without a suite cannot record a baseline (step 1.4) and cannot compare before and after
    (step 4.7), so it would end in BLOCKED after the agent has already written a failing test.
    Stopping at step 0 is cheaper, and it is what the spec already says.
    """
    _no_suite_contract(tmp_path)
    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 2, "a project with no suite was reported as usable"
    combined = result.stdout + result.stderr
    assert "no full suite" in combined.lower() or "suite" in combined.lower(), combined


def test_no_suite_refusal_offers_three_concrete_ways_forward(
    tmp_path: Path, empty_home: Path
) -> None:
    """The operator's requirement: the user sees the problem and three ways to fix it at once.

    A refusal that only says "no" leaves the user to work out what to do. Three concrete options
    turn a dead end into a decision, and the first of them is what the agent should propose.
    """
    _no_suite_contract(tmp_path)
    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    combined = result.stdout + result.stderr
    numbered = [line for line in combined.splitlines() if re.match(r"^\s*\d+[.)]\s", line)]
    assert len(numbered) >= 3, f"the refusal lists {len(numbered)} option(s): {combined}"
    lowered = combined.lower()
    assert "write" in lowered or "add" in lowered, "no option mentions writing tests"
    assert "existing" in lowered or "already" in lowered, (
        "no option mentions an existing suite or another way to verify"
    )


def test_gate_refuses_work_in_a_project_with_no_suite(tmp_path: Path, empty_home: Path) -> None:
    """A verified contract with `Full suite: none` still cannot support a run."""
    _no_suite_contract(tmp_path)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2
    message = json.loads(result.stdout)["agent_message"]
    assert "suite" in message.lower(), message


def test_a_refused_run_records_the_problem_for_the_user(
    tmp_path: Path, empty_home: Path
) -> None:
    """The refusal is for the user, not only the agent: it has to be readable on its own.

    The message names the missing thing, why the plugin cannot work without it, and what to do —
    in that order. An agent relaying "no suite" alone leaves the user no better off.
    """
    _no_suite_contract(tmp_path)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "TASK-1.md").write_text("## Task\nx\n", encoding="utf-8")
    result = _run_gate(_WRITE, tmp_path)
    message = json.loads(result.stdout)["agent_message"]
    assert len(message) > 200, f"the refusal is too short to explain anything: {message}"
    assert "baseline" in message.lower() or "compare" in message.lower(), (
        "the refusal does not say why a suite is needed"
    )


# --- schema drift has a documented fix -------------------------------------


def test_init_documents_how_to_migrate_an_old_contract() -> None:
    """A version warning is only useful if it says what to do.

    The contract holds the project's decisions, so regeneration cannot simply overwrite it. The
    procedure is: generate beside it, carry the values over, confirm again.
    """
    reference = _SKILL / "references" / "init.md"
    text = reference.read_text(encoding="utf-8")
    assert "Contract version" in text
    assert "--force" in text, "no procedure for regenerating an outdated contract"
    assert "verified" in text.lower(), "the migration does not mention re-confirming"


def test_the_doctor_version_warning_points_at_the_documented_procedure(
    tmp_path: Path, empty_home: Path
) -> None:
    """The warning has to lead to the instructions, not to a guess."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Contract version: 0\n"
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\n"
        "Allowed: none\nContract verified by: agent 2026-11-27\n\n"
        "## Extra prohibitions\n\n- no sudo\n",
        encoding="utf-8",
    )
    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    combined = result.stdout + result.stderr
    assert "references/init.md" in combined or "bugfixer init" in combined, combined


def test_init_fills_prohibitions_with_the_safe_baseline(tmp_path: Path, empty_home: Path) -> None:
    """Prohibitions default to the rules that are true of every project, not to none.

    ``no sudo``, no destructive commands and no secrets in queries are not project decisions:
    they are the spec's own §15 prohibitions, so starting from them is correct rather than
    presumptuous.
    """
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    for rule in ("sudo", "destructive", "secrets"):
        assert rule in text.lower(), f"the default prohibitions omit {rule!r}"
    assert "<what this project forbids" not in text


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("Makefile", "make test"),
        ("pyproject.toml", "pytest"),
        ("Cargo.toml", "cargo test"),
        ("package.json", "npm test"),
        ("go.mod", "go test ./..."),
    ],
)
def test_init_detects_the_test_command_from_the_project(
    tmp_path: Path, marker: str, expected: str
) -> None:
    """The command is read from a manifest, not guessed: a manifest *is* the fact.

    ``§0`` forbids inventing a command. A ``Makefile`` with a ``test`` target, a
    ``pyproject.toml``, a ``Cargo.toml`` — each says which toolchain runs this project, and that
    is evidence rather than a guess. An empty project gets no command at all, which is the test
    above.
    """
    if marker == "Makefile":
        (tmp_path / marker).write_text("test:\n\tpytest -q\n", encoding="utf-8")
    elif marker == "package.json":
        (tmp_path / marker).write_text('{"scripts": {"test": "jest"}}\n', encoding="utf-8")
    else:
        (tmp_path / marker).write_text("\n", encoding="utf-8")

    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    assert expected in text, f"{marker} was present but the contract does not name {expected!r}"


def test_a_detected_contract_is_usable_after_one_confirmation(
    tmp_path: Path, empty_home: Path
) -> None:
    """Out of the box means: generated, confirmed once, then working.

    The operator's rule — configure once, then work. The confirmation is one edit to one line;
    after it the gate enforces real rules rather than refusing for want of a contract. Anything
    less would mean enforcing a command nobody ran.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "TASK-1.md").write_text("## Task\nfix\n", encoding="utf-8")

    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0

    contract = tmp_path / "bug-fixer.project.md"
    assert "Contract verified by: not yet" in contract.read_text(encoding="utf-8")
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(
            "Contract verified by: not yet",
            "Contract verified by: agent 2026-11-27 — pytest ran, 12 passed",
        ),
        encoding="utf-8",
    )

    doctor = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr

    result = _run_gate(_WRITE, tmp_path)
    assert result.returncode == 2
    assert "Phase 1" in json.loads(result.stdout)["agent_message"], (
        "the gate did not reach its real rules, so the contract is not actually usable"
    )


def test_init_refuses_to_overwrite_an_existing_contract(tmp_path: Path, empty_home: Path) -> None:
    """A filled contract holds decisions; replacing it silently would destroy them."""
    contract = tmp_path / "bug-fixer.project.md"
    contract.write_text("# hand-written\nFull suite: make test\n", encoding="utf-8")
    result = _run_cli("init", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 2
    assert "exists" in (result.stdout + result.stderr).lower()
    assert contract.read_text(encoding="utf-8").startswith("# hand-written")


def test_init_can_force_after_an_explicit_request(tmp_path: Path, empty_home: Path) -> None:
    """Overwriting is allowed when asked for by name, never by accident."""
    contract = tmp_path / "bug-fixer.project.md"
    contract.write_text("# hand-written\n", encoding="utf-8")
    result = _run_cli("init", "--project", str(tmp_path), "--force")
    assert result.returncode == 0, result.stderr
    assert "Task/bug file:" in contract.read_text(encoding="utf-8")


def test_a_freshly_initialised_contract_is_not_yet_usable(tmp_path: Path, empty_home: Path) -> None:
    """`doctor` must say what is missing, rather than bless a contract nobody finished."""
    assert _run_cli("init", "--project", str(tmp_path), home=empty_home).returncode == 0
    result = _run_cli("doctor", "--project", str(tmp_path), home=empty_home)
    assert result.returncode == 2, "an unfinished contract was reported as usable"
    combined = result.stdout + result.stderr
    assert "Task/bug file" in combined or "not been verified" in combined.lower(), combined


# --- the gate: a run without a usable contract is refused -------------------


def test_the_gate_refuses_a_project_with_no_contract(tmp_path: Path, empty_home: Path) -> None:
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


def test_the_gate_refuses_a_contract_with_placeholders(tmp_path: Path, empty_home: Path) -> None:
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


def test_a_completed_contract_lets_the_gate_work_normally(tmp_path: Path, empty_home: Path) -> None:
    """The legal path stays open: a real contract gives a real verdict, not a blanket refusal."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "Task/bug file: tasks/TASK-*.md\nFull suite: make test\nTest paths: tests/\nContract verified by: agent 2026-11-27 — confirmed\n"
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


def test_creating_the_contract_itself_is_never_refused(tmp_path: Path, empty_home: Path) -> None:
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


def test_installing_the_plugin_is_still_possible_without_a_contract(tmp_path: Path, empty_home: Path) -> None:
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


def test_a_project_install_writes_a_starting_contract(tmp_path: Path, empty_home: Path) -> None:
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


def test_a_project_install_does_not_overwrite_a_hand_written_contract(tmp_path: Path, empty_home: Path) -> None:
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

