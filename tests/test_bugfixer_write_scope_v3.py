"""What a run may write in each phase (spec §5.5, invariants 45–49; added 2026-11-27).

The rule was "everything outside `Test paths:` is production code", and that inference was the
defect: it refused to record logs, reports and documentation, so a run could not produce the
evidence it exists to produce. The tables below are the spec's explicit allow-list, checked as a
table rather than one path at a time — a missing exemption shows up as a failing row instead of as
a run that mysteriously cannot write its own baseline.

Both directions are tested. A rule that allows everything passes every "is allowed" case, so each
exemption has a neighbour that must still be refused.
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
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\nRaw dumps: logs/dumps/\n"
    "Docs paths: docs/\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "Coverage command: coverage report\nMin coverage: 90\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)


@pytest.fixture()
def gate():
    """The gate module, imported as the plugin ships it."""
    if str(_HOOKS) not in sys.path:
        sys.path.insert(0, str(_HOOKS))
    return importlib.import_module("bugfixer_gate")


def _project(tmp_path: Path, phase: int) -> Path:
    """A project in a given phase, with a contract and a ledger.

    Args:
        tmp_path: Scratch directory.
        phase: Phase to record in the journal.

    Returns:
        The ledger path.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    ledger = tmp_path / "tasks" / "TASK-1.md"
    ledger.write_text(
        "## Task\nfix\n\n## Clarifications\nQ: a  A: b\nQ: c  A: d\nQ: e  A: f\n\n"
        "FAILING TEST: pytest -> 1 failed\nBASELINE: 5/5\n",
        encoding="utf-8",
    )
    jrnl.set_phase(ledger, phase)
    return ledger


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project in phase 1, where the ban is strictest."""
    return _project(tmp_path, 1)


# --- invariant 45: logs are never refused -----------------------------------


@pytest.mark.parametrize("phase", (1, 2, 3, 5))
@pytest.mark.parametrize(
    "target",
    [
        "logs/dumps/run.log",
        "logs/anything/pass-01.txt",
        "logs/bugfixer-run-1/pass-01/tests.log",
        "logs/app.log",
    ],
)
def test_a_log_is_writable_in_every_phase(
    gate, tmp_path: Path, phase: int, target: str
) -> None:
    """Without this the full test output of step 1.4 has nowhere to go.

    §5.3 puts output too large for the task file in the raw-dump directory, and §5.4 says the full
    output goes there rather than into the ledger. Refusing it made both impossible.
    """
    ledger = _project(tmp_path, phase)
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": target})
    assert verdict.allow, verdict.reason


def test_the_raw_dump_directory_from_the_contract_is_writable(gate, project: Path) -> None:
    """The contract names the directory; the gate must honour the name it read."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "logs/dumps/deep.log"})
    assert verdict.allow, verdict.reason


# --- invariant 46: documentation is data ------------------------------------


@pytest.mark.parametrize(
    "target",
    ["README.md", "docs/notes.md", "notes.txt", "docs/guide.rst", "data/report.csv", "x.log"],
)
def test_prose_and_data_are_writable_in_phase_one(gate, project: Path, target: str) -> None:
    """§2.3 asks the agent to document assumptions; a phase that forbids it cannot comply."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": target})
    assert verdict.allow, verdict.reason


@pytest.mark.parametrize(
    "target",
    ["src/app.py", "pyproject.toml", "package.json", "Makefile", "deploy.yaml", "Cargo.lock"],
)
def test_behaviour_files_are_still_refused_in_phase_one(gate, project: Path, target: str) -> None:
    """The negative control, and the reason the allow-list is by extension.

    `.toml` and `.json` look like data but change how the project builds and runs, so they stay on
    the code side.
    """
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": target})
    assert not verdict.allow, f"{target} was treated as documentation"
    assert "Phase 1" in verdict.reason


# --- invariant 47: the plugin's own tree needs a flag -----------------------


def test_editing_the_plugin_tree_is_refused_without_the_flag(gate, project: Path) -> None:
    """A run that can rewrite its own guard has no guard."""
    verdict = gate.decide(
        ledger=project, tool="write", args={"file_path": "skills/bug-fixer/hooks/bugfixer_gate.py"}
    )
    assert not verdict.allow
    assert "Phase 1" in verdict.reason


def test_the_dev_plugin_flag_allows_the_plugin_tree(gate, project: Path) -> None:
    """Developing the plugin with its own gate installed is the documented reason for the flag."""
    verdict = gate.decide(
        ledger=project,
        tool="write",
        args={"file_path": "skills/bug-fixer/hooks/bugfixer_gate.py"},
        dev_plugin=True,
    )
    assert verdict.allow, verdict.reason


def test_the_flag_does_not_open_the_projects_code(gate, project: Path) -> None:
    """A flag for one job must not become a skeleton key."""
    verdict = gate.decide(
        ledger=project, tool="write", args={"file_path": "src/app.py"}, dev_plugin=True
    )
    assert not verdict.allow


def test_the_flag_is_only_a_cli_argument(gate) -> None:
    """It cannot be reached from the payload: a harness must not be able to grant it."""
    parser_source = (_HOOKS / "bugfixer_gate.py").read_text(encoding="utf-8")
    assert '"--dev-plugin"' in parser_source
    assert 'event.get("dev_plugin")' not in parser_source
    assert "tool_input.get(\"dev_plugin\")" not in parser_source


# --- invariant 48: the installer's files ------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "bug-fixer.project.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".gitignore",
        ".cursor/rules/bug-fixer.mdc",
        ".cursor/hooks.json",
    ],
)
def test_the_installers_files_are_writable_in_phase_one(gate, project: Path, target: str) -> None:
    """Without them the plugin cannot install, and installation happens in any phase."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": target})
    assert verdict.allow, verdict.reason


# --- invariant 49: the changelog is a claim ---------------------------------


def _changelog_args(line: str) -> dict:
    """A write payload for `CHANGELOG.md` carrying one entry line."""
    return {"file_path": "CHANGELOG.md", "content": f"## Unreleased\n\n- fixed the thing\n{line}\n"}


def test_the_changelog_is_refused_before_a_verdict(gate, project: Path) -> None:
    """It says a change is finished; before phase 4 that is not known."""
    verdict = gate.decide(ledger=project, tool="write", args=_changelog_args("tested: 95%"))
    assert not verdict.allow
    assert "verdict" in verdict.reason.lower()


def test_the_changelog_is_refused_without_a_coverage_number(gate, project: Path) -> None:
    """`tested:` is the whole point of the entry: it says the change is covered."""
    ledger = project
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + "\n### F9 — probe\n\n**Verdict.** CONFIRMED — the cause was X.\\n\\nФАКТ (GREEN): pytest -> 12 passed\\n",
        encoding="utf-8",
    )
    verdict = gate.decide(ledger=ledger, tool="write", args=_changelog_args(""))
    assert not verdict.allow
    assert "tested:" in verdict.reason


def test_the_changelog_is_refused_below_the_threshold(gate, project: Path) -> None:
    """Under 90% the change is not covered enough to be recorded as done (invariant 49)."""
    ledger = project
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + "\n### F9 — probe\n\n**Verdict.** CONFIRMED — the cause was X.\\n\\nФАКТ (GREEN): pytest -> 12 passed\\n",
        encoding="utf-8",
    )
    verdict = gate.decide(ledger=ledger, tool="write", args=_changelog_args("tested: 72%"))
    assert not verdict.allow
    assert "90%" in verdict.reason
    assert "TDD" in verdict.reason or "tests first" in verdict.reason


def test_the_changelog_is_allowed_at_the_threshold(gate, project: Path) -> None:
    """The positive case: verdict, number, threshold met, command present."""
    ledger = project
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + "\n### F9 — probe\n\n**Verdict.** CONFIRMED — the cause was X.\\n\\nФАКТ (GREEN): pytest -> 12 passed\\n",
        encoding="utf-8",
    )
    verdict = gate.decide(ledger=ledger, tool="write", args=_changelog_args("tested: 92%"))
    assert verdict.allow, verdict.reason


def test_the_changelog_needs_a_way_to_verify_the_number(gate, tmp_path: Path) -> None:
    """Without a coverage command the number is a claim nobody checked (§0)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(
        CONTRACT.replace("Coverage command: coverage report\n", ""), encoding="utf-8"
    )
    ledger = tmp_path / "tasks" / "TASK-1.md"
    ledger.write_text(
        "## Task\nfix\n\n**Verdict.** CONFIRMED — cause.\\n\\nФАКТ (GREEN): pytest -> 12 passed\\n", encoding="utf-8"
    )
    jrnl.set_phase(ledger, 4)
    verdict = gate.decide(ledger=ledger, tool="write", args=_changelog_args("tested: 95%"))
    assert not verdict.allow
    assert "Coverage command" in verdict.reason


# --- the contract grew three fields, without becoming a fifth point ---------


def test_the_new_fields_do_not_add_a_fifth_required_point() -> None:
    """§6 fixes four points; `Docs paths:`, `Min coverage:` and `Coverage command:` are details.

    The existing contract tests assert the required set, so this test only says the new fields
    exist and are optional — the boundary the spec cares about is asserted next to the schema.
    """
    if str(jrnl.SKILL_DIR / "lib") not in sys.path:
        sys.path.insert(0, str(jrnl.SKILL_DIR / "lib"))
    contract = importlib.import_module("bugfixer.contract")
    for name in ("Docs paths", "Min coverage", "Coverage command"):
        assert name in contract.FIELD_POINTS, name
        assert not contract.FIELD_POINTS[name].required, f"{name} must stay optional"
    assert contract.REQUIRED_FIELDS == (
        "Task/bug file",
        "Full suite",
        "Test paths",
        "Search",
        "Prohibitions",
    )


# --- the readers behind the new fields --------------------------------------


def _contract_module():
    """The contract schema module."""
    if str(jrnl.SKILL_DIR / "lib") not in sys.path:
        sys.path.insert(0, str(jrnl.SKILL_DIR / "lib"))
    return importlib.import_module("bugfixer.contract")


def test_docs_paths_reads_a_list_and_tolerates_absence() -> None:
    """A project without the field still gets an answer: an empty list, not a crash."""
    contract = _contract_module()
    assert contract.docs_paths("Docs paths: docs/, notes/\n") == ["docs/", "notes/"]
    assert contract.docs_paths("Docs paths: `docs/`\n") == ["docs/"]
    assert contract.docs_paths("no fields here\n") == []


def test_min_coverage_defaults_to_the_spec_number() -> None:
    """The default belongs to §11a; a plugin constant that drifted from it would be a second norm."""
    contract = _contract_module()
    assert contract.min_coverage("") == contract.DEFAULT_MIN_COVERAGE == 90
    assert contract.min_coverage("Min coverage: 75\n") == 75
    assert contract.min_coverage("Min coverage: 95%\n") == 95
    assert contract.min_coverage("Min coverage: nonsense\n") == 90


def test_coverage_command_is_empty_when_the_project_names_none() -> None:
    """Empty is meaningful: the gate then refuses to verify a `tested:` claim (§0)."""
    contract = _contract_module()
    assert contract.coverage_command("") == ""
    assert contract.coverage_command("Coverage command: coverage report\n") == "coverage report"


def test_a_weak_directory_name_needs_test_files_inside() -> None:
    """`spec/` is a guess; `spec/` with a test file is a fact (measured defect, 2026-11-27)."""
    contract = _contract_module()
    import tempfile

    root = Path(tempfile.mkdtemp())
    (root / "spec").mkdir()
    assert contract._is_test_dir(root, "spec") is False
    (root / "spec" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    assert contract._is_test_dir(root, "spec") is True


def test_a_strong_directory_name_is_accepted_without_files(tmp_path: Path) -> None:
    """`tests/` means tests in a fresh checkout, before the first file exists."""
    contract = _contract_module()
    (tmp_path / "tests").mkdir()
    assert contract._is_test_dir(tmp_path, "tests") is True
    assert contract._is_test_dir(tmp_path, "__tests__") is True


def test_docs_paths_detection_offers_the_root_when_markdown_is_there(tmp_path: Path) -> None:
    """Root prose is nearly universal, and refusing it would refuse doc writes everywhere."""
    contract = _contract_module()
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    assert contract._detect_docs_paths(tmp_path) == "., docs/"
    empty = Path(tmp_path / "empty")
    empty.mkdir()
    assert contract._detect_docs_paths(empty).startswith("<")


def test_coverage_command_detection_reads_the_manifest(tmp_path: Path) -> None:
    """A configured tool is evidence; its absence is a placeholder, not a guess."""
    contract = _contract_module()
    assert contract._detect_coverage_command(tmp_path).startswith("<")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.run]\nsource=['x']\n", encoding="utf-8"
    )
    assert "coverage" in contract._detect_coverage_command(tmp_path)


def test_init_writes_the_three_new_fields(tmp_path: Path) -> None:
    """The template and the validator share one schema; a field one knows and the other does not
    is the defect that made a template-shaped contract turn the gate off."""
    import subprocess

    (tmp_path / "tests").mkdir()
    result = subprocess.run(
        [sys.executable, str(jrnl.SKILL_DIR / "bin" / "bugfixer"), "init", "--project", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "bug-fixer.project.md").read_text(encoding="utf-8")
    for field in ("Docs paths:", "Coverage command:", "Min coverage:"):
        assert field in text, field


# --- the gate's own guards around the new rules -----------------------------


def test_a_doc_write_with_no_readable_target_is_not_documentation(gate) -> None:
    """An empty target cannot be shown to be prose, so it falls through to the code rule."""
    assert gate._is_doc_write("", None, Path("/project"), "write") is False


def test_a_log_write_with_no_readable_target_is_not_a_dump(gate, project: Path) -> None:
    """Same rule for logs: silence is not a destination."""
    assert gate._is_raw_dump_write("", project, "write") is False


def test_a_changelog_edit_without_content_is_refused(gate, project: Path) -> None:
    """An edit that does not carry the new text cannot be checked, so it is not assumed fine."""
    ledger = project
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + "\n**Verdict.** CONFIRMED — cause.\\n\\nФАКТ (GREEN): pytest -> 12 passed\\n",
        encoding="utf-8",
    )
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "CHANGELOG.md"})
    assert not verdict.allow
    assert "does not carry the new text" in verdict.reason


# --- the run has to be green in full, not 90% of it -------------------------


def _with_green(gate, ledger: Path, green_line: str, extra: str = "") -> object:
    """Append a verdict and one green marker, then ask to write the changelog."""
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + f"\n**Verdict.** CONFIRMED — cause.\n\n{green_line}\n{extra}",
        encoding="utf-8",
    )
    return gate.decide(
        ledger=ledger,
        tool="write",
        args={"file_path": "CHANGELOG.md", "content": "- fix\ntested: 95%\n"},
    )


def test_a_green_run_allows_the_entry(gate, project: Path) -> None:
    """The positive case: every test passed, coverage above threshold."""
    verdict = _with_green(gate, project, "ФАКТ (GREEN): pytest -> 12 passed")
    assert verdict.allow, verdict.reason


@pytest.mark.parametrize(
    "line",
    [
        "ФАКТ (GREEN): pytest -> 3 failed, 9 passed",
        "ФАКТ (GREEN): pytest -> 1 error",
        "ФАКТ (GREEN): make test -> FAILED (2 failures)",
        "ФАКТ (GREEN): npm test -> 12 passed, 1 failing",
    ],
)
def test_a_partially_failing_run_is_not_green(gate, project: Path, line: str) -> None:
    """§11a: the threshold is about coverage, not about the share of tests that passed.

    A suite where 90% of tests pass is a failing suite. An entry written then would claim the
    change works while the run says it does not.
    """
    verdict = _with_green(gate, project, line)
    assert not verdict.allow
    assert "every test has to pass" in verdict.reason.lower() or "failing" in verdict.reason.lower()


def test_no_green_marker_at_all_is_refused(gate, project: Path) -> None:
    """A verdict without a run is not evidence that the suite passes."""
    verdict = _with_green(gate, project, "")
    assert not verdict.allow
    assert "ФАКТ (GREEN):" in verdict.reason


def test_a_regression_after_the_green_run_is_refused(gate, project: Path) -> None:
    """A green marker before a later regression describes a tree that no longer exists (§9)."""
    verdict = _with_green(
        gate,
        project,
        "ФАКТ (GREEN): pytest -> 12 passed",
        extra="\nФАКТ (регрессия): test_baseline\n",
    )
    assert not verdict.allow
    assert "регрессия" in verdict.reason


def test_a_regression_before_the_green_run_does_not_block(gate, project: Path) -> None:
    """Order decides: a regression already fixed by the green run is history."""
    ledger = project
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + "\n**Verdict.** CONFIRMED — cause.\n\nФАКТ (регрессия): test_baseline\n"
        "\nФАКТ (GREEN): pytest -> 12 passed\n",
        encoding="utf-8",
    )
    verdict = gate.decide(
        ledger=ledger,
        tool="write",
        args={"file_path": "CHANGELOG.md", "content": "- fix\ntested: 95%\n"},
    )
    assert verdict.allow, verdict.reason

