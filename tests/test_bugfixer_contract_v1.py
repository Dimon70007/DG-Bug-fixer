"""Contract v1: one schema that the template, the validator and the gate agree on.

The defect this closes was measured, not suspected. A contract written the way the plugin's
own template teaches produced:

    ledger pattern : '<path or pattern, e.g. tasks/TASK-*.md'
    test paths     : []
    discover_ledger -> None
    gate on a production write: allow

That is the worst failure mode a guard can have: the agent fills in the contract exactly as
instructed, the gate cannot find the ledger, and it turns itself off without a word.

`Test paths:` is the one field the spec's §6 never mentions, and the gate cannot enforce §1
without it. It is required as a **detail of point 2** ("the full suite decides what counts as
a test"), not as a fifth point: the four points of §6 stay exactly four.
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
_CONTRACT_LIB = _SKILL / "lib" / "bugfixer" / "contract.py"
_CLI = _SKILL / "bin" / "bugfixer"
_TEMPLATE_DOC = _SKILL / "references" / "project-contract.md"


def _load(name: str, path: Path):
    """Import a module from a file path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def contract():
    """The contract schema module."""
    return _load("bugfixer_contract_v1", _CONTRACT_LIB)


def _run_doctor(project: Path) -> subprocess.CompletedProcess[str]:
    """Run ``bugfixer doctor`` the way an operator would."""
    return subprocess.run(
        [sys.executable, str(_CLI), "doctor", "--project", str(project)],
        text=True,
        capture_output=True,
        check=False,
    )


def _template_block() -> str:
    """The markdown block the template tells a project to copy.

    Returns:
        The block verbatim, so tests exercise what a user actually copies.
    """
    text = _TEMPLATE_DOC.read_text(encoding="utf-8")
    match = re.search(r"```markdown\n(.*?)```", text, re.DOTALL)
    assert match, "the contract template has no ```markdown block to copy"
    return match.group(1)


def _filled_template() -> str:
    """The template block with every placeholder replaced by a plausible value.

    Substitution is deliberately mechanical: it proves the *structure* of the template is
    something the validator accepts, without hand-writing a second contract that could drift
    from the first.

    Returns:
        A contract a real project would produce.
    """
    replacements = {
        "<project name>": "billing-service",
        "<glob or path, e.g. tasks/TASK-*.md; alternatives may follow as `a | b`>": "tasks/TASK-*.md",
        "<directory for logs too large for the task file, e.g. logs/dumps/>": "logs/dumps/",
        "<the command the gate reruns, e.g. make test>": "make test",
        "<comma-separated prefixes that are tests, e.g. tests/, pkg/tests/>": "tests/, pkg/tests/",
        "<e.g. pytest tests/test_foo.py -v>": "pytest tests/test_foo.py -v",
        "<e.g. cargo test -p my-crate>": "pytest -m unit",
        "<e.g. ruff check . && mypy>": "ruff check .",
        "<e.g. tavily_search, agent-search MCP — or `none`>": "tavily_search",
        "<e.g. curl, wget, WebFetch to external hosts>": "curl, wget",
        "<e.g. no sudo from the agent; print the command for the operator>": "no sudo",
        "<e.g. no destructive commands: no recursive deletes outside the build dir, no docker prune>": "no docker prune",
        "<e.g. dependencies only via poetry; never pip install into a system interpreter>": "poetry only",
        "<e.g. never send secrets, tokens, or PII to a search query or a cloud model>": "no PII in queries",
    }
    block = _template_block()
    for placeholder, value in replacements.items():
        block = block.replace(placeholder, value)
    return block


# --- the schema is the spec's four points ------------------------------------


def test_the_required_points_are_the_four_of_the_spec(contract) -> None:
    """§6 phase 0 names four things; a fifth would be the plugin inventing a requirement."""
    assert contract.SPEC_POINTS == (
        "ledger",
        "full_suite",
        "search",
        "prohibitions",
    ), "the required points no longer match spec §6 phase 0"


def test_test_paths_is_a_detail_of_the_full_suite_point(contract) -> None:
    """`Test paths:` is required, but it belongs to point 2 rather than beside it.

    The gate cannot tell a test edit from a production edit without it (§1), and §0 forbids
    guessing a path. The spec does not mention the field, so it lives inside "what the full
    suite is" instead of becoming a fifth point.
    """
    detail = contract.FIELD_POINTS["Test paths"]
    assert detail.point == "full_suite"
    assert detail.required, "the gate fails closed without Test paths:, so it cannot be optional"


@pytest.mark.parametrize(
    "field",
    ["Task/bug file", "Full suite", "Test paths", "Search", "Prohibitions"],
)
def test_every_required_field_maps_to_one_of_the_four_points(contract, field: str) -> None:
    """No field may exist outside the four points, or the contract has grown a fifth."""
    assert contract.FIELD_POINTS[field].point in contract.SPEC_POINTS


def test_raw_dumps_is_optional(contract) -> None:
    """§5.3 says the path comes from the contract, but §6 never makes it one of the four."""
    assert contract.FIELD_POINTS["Raw dumps"].required is False


# --- the template is inside the validator ------------------------------------


def test_the_template_block_filled_in_passes_the_validator(contract) -> None:
    """Principle 3: a contract built from the template must be a contract the gate accepts.

    The old template omitted `Test paths:` entirely, so every project that followed it had a
    gate that refused phase-1 test writes and, worse, could not find the ledger at all.
    """
    problems = contract.problems(_filled_template())
    assert problems == [], f"a template-shaped contract is rejected: {problems}"


def test_the_unfilled_template_is_rejected_with_the_field_named(contract) -> None:
    """Placeholders are not values; telling the agent which field is empty is the point."""
    problems = contract.problems(_template_block())
    assert problems, "an unfilled template passed as a valid contract"
    assert any("Task/bug file" in problem for problem in problems), problems


def test_every_field_the_validator_requires_is_taught_by_the_template(contract) -> None:
    """A required field the template never shows is a field nobody can fill in.

    Matched through the schema's own name patterns, not by literal string: ``Prohibitions``
    is satisfied by the template's ``## Extra prohibitions for this project`` heading, and a
    literal check would call that a missing field.
    """
    block = _template_block()
    missing = [
        field_name
        for field_name, rule in contract.FIELD_POINTS.items()
        if rule.required
        and rule.names.search(block) is None
        and not (rule.section is not None and rule.section.search(block))
    ]
    assert not missing, f"the template never teaches required fields: {missing}"


# --- §6 point 2: the full suite and its detail -------------------------------


def test_a_contract_without_test_paths_names_the_point_it_belongs_to(contract) -> None:
    """The refusal has to say *why* the field exists, or it reads as an arbitrary demand."""
    text = _filled_template().replace("Test paths:", "Paths (unused):")
    problems = contract.problems(text)
    assert problems, "a contract without Test paths: was accepted"
    combined = " ".join(problems)
    assert "Test paths" in combined
    assert "full suite" in combined.lower(), (
        f"the refusal does not tie the field to point 2 of §6: {combined}"
    )


def test_raw_dumps_absence_is_a_warning_not_a_failure(contract) -> None:
    """§5.3 allows the logs to live in the task file; a warning is the honest signal."""
    text = "\n".join(
        line for line in _filled_template().splitlines() if not line.startswith("Raw dumps:")
    )
    assert contract.problems(text) == []
    warnings = contract.warnings(text)
    assert any("Raw dumps" in warning for warning in warnings), warnings


# --- doctor ------------------------------------------------------------------


def test_doctor_accepts_a_template_shaped_contract(tmp_path: Path) -> None:
    """Exit 0, and the output says the contract is usable."""
    (tmp_path / "bug-fixer.project.md").write_text(_filled_template(), encoding="utf-8")
    result = _run_doctor(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout.lower() or "ready" in result.stdout.lower(), result.stdout


def test_doctor_refuses_a_contract_missing_one_of_the_four_points(tmp_path: Path) -> None:
    """Exit 2 with the field named — the operator has to know what to add."""
    text = _filled_template()
    text = re.sub(r"^## Search.*?(?=^## )", "", text, flags=re.DOTALL | re.MULTILINE)
    (tmp_path / "bug-fixer.project.md").write_text(text, encoding="utf-8")
    result = _run_doctor(tmp_path)
    assert result.returncode == 2
    assert "Search" in result.stdout + result.stderr


def test_doctor_warns_about_raw_dumps_but_exits_zero(tmp_path: Path) -> None:
    """An optional field that is absent is a warning; turning it into a failure invents a rule."""
    text = "\n".join(
        line for line in _filled_template().splitlines() if not line.startswith("Raw dumps:")
    )
    (tmp_path / "bug-fixer.project.md").write_text(text, encoding="utf-8")
    result = _run_doctor(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Raw dumps" in result.stdout + result.stderr


def test_doctor_without_any_contract_fails_closed(tmp_path: Path) -> None:
    """No contract means phase 0 cannot start; reporting success would be a lie."""
    result = _run_doctor(tmp_path)
    assert result.returncode == 2
    assert "bug-fixer.project.md" in result.stdout + result.stderr


# --- the defect that started this stage --------------------------------------


def test_a_template_shaped_contract_leaves_the_gate_active(tmp_path: Path) -> None:
    """The measured defect: a template contract used to turn the gate off silently.

    With the ledger pattern parsed as the literal placeholder text, `discover_ledger` found
    nothing, `decide` answered "no run found" and every production write was allowed. A
    contract that follows the template has to bind the gate to the ledger.
    """
    (tmp_path / "bug-fixer.project.md").write_text(_filled_template(), encoding="utf-8")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    ledger = tasks / "TASK-100.md"
    ledger.write_text("## Task\nfix\n", encoding="utf-8")

    gate = _load("bugfixer_gate_contract_v1", jrnl.HOOKS / "bugfixer_gate.py")
    found = gate.discover_ledger(tmp_path)
    assert found is not None, "the gate cannot find the ledger from a template-shaped contract"
    assert found.resolve() == ledger.resolve()

    verdict = gate.decide(ledger=found, tool="write", args={"file_path": "src/core.py"})
    assert not verdict.allow, "the gate was inert while a run was active"


def test_doctor_reports_where_the_gate_would_look(tmp_path: Path) -> None:
    """`doctor` and the gate must answer the same question the same way.

    Two readers of one file that disagree is how a contract looks valid and still leaves the
    gate inactive. The output names the resolved ledger so the difference is visible.
    """
    (tmp_path / "bug-fixer.project.md").write_text(_filled_template(), encoding="utf-8")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "TASK-100.md").write_text("## Task\nfix\n", encoding="utf-8")

    result = _run_doctor(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TASK-100.md" in result.stdout, "doctor does not report the resolved ledger"
