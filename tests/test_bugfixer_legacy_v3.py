"""Legacy ledger detection (TASK-081 ST-8).

A 2.1 ledger is not a 3.0 ledger in phase 1. Its history has no ``### H<n>``, so
every 3.0 guard refuses it — and each refusal looks like a bug in the gate rather
than a stale file. The refusal has to say which it is.

The detector must be narrow: 3.0 records carry an ``Iteration`` *field*, so a
heading-only check is safe only because the hypothesis test backs it up.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_GATE = jrnl.HOOKS / "bugfixer_gate.py"


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


@pytest.fixture(scope="module")
def gate():
    """The gate module."""
    return _load("bugfixer_gate_legacy", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a contract so writes can be classified."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    return tmp_path


def _decide(gate, ledger: Path):
    """Run the decision function for a production write."""
    return gate.decide(ledger=ledger, tool="write", args={"file_path": "src/x.py"})


# --- the detector ------------------------------------------------------------


def test_iteration_heading_without_hypotheses_is_legacy(gate) -> None:
    """The 2.1 signature: a pass heading and no hypotheses anywhere."""
    assert gate.legacy_ledger("## Iteration 1\n\n### F1 — baseline\n") is True


def test_three_zero_ledger_is_not_legacy(gate) -> None:
    """One ``### H1`` is enough to make it a 3.0 run, whatever else it holds."""
    text = "## Iteration 1\n\n### H1 — claim\n| Rank | 1 |\n"
    assert gate.legacy_ledger(text) is False


def test_iteration_field_in_a_block_is_not_a_heading(gate) -> None:
    """3.0 keeps the pass in a field; only the heading is the old signature."""
    text = (
        "### F1 — baseline\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| Kind | research |\n"
        "| Iteration | 1 |\n"
        "\n### H1 — claim\n| Rank | 1 |\n"
    )
    assert gate.legacy_ledger(text) is False


def test_empty_ledger_is_not_legacy(gate) -> None:
    """A fresh file is the start of new work, not a stale one."""
    assert gate.legacy_ledger("") is False
    assert gate.legacy_ledger("## Task\nfix the cache\n") is False


def test_fresh_three_zero_ledger_is_not_legacy(gate) -> None:
    """Phase 1 of a legitimate 3.0 run writes ``## Iteration 1`` before any hypothesis.

    The spec's literal rule (heading + no ``### H<n>``) would refuse every new run at
    its first step. This is the regression that rule produced.
    """
    fresh = (
        "## Task\nfix it\n\n## Iteration 1\n\n"
        "FAILING TEST: pytest tests/x.py -> 1 failed\n"
        "BASELINE: 10/10\n\n"
        "### F1 — baseline\n| Field | Value |\n|---|---|\n| Kind | research |\n"
    )
    assert gate.legacy_ledger(fresh) is False


def test_three_zero_record_without_hypotheses_is_not_legacy(gate) -> None:
    """A ``Kind`` field is the 3.0 signature: 2.1 had no such field."""
    text = "## Iteration 1\n\n### F1 — baseline\n| Field | Value |\n|---|---|\n| Kind | research |\n"
    assert gate.legacy_ledger(text) is False


def test_prose_mention_of_iteration_is_not_a_heading(gate) -> None:
    """Describing the heading must not make a 3.0 ledger look legacy."""
    text = "The old format wrote `## Iteration 1` at the top.\n\n### H1 — claim\n"
    assert gate.legacy_ledger(text) is False


# --- the refusal -------------------------------------------------------------


def test_legacy_ledger_denies_a_write_with_an_actionable_reason(gate, project: Path) -> None:
    """The refusal must name both ways out: finish under 2.1, or start a new ledger."""
    ledger = project / "OLD.md"
    ledger.write_text("## Iteration 1\n\n### F1 — baseline\n", encoding="utf-8")

    verdict = _decide(gate, ledger)

    assert not verdict.allow
    assert "2.1" in verdict.reason
    assert "new ledger" in verdict.reason
    assert "### H<n>" in verdict.reason


def test_legacy_ledger_is_refused_for_reads_too(gate, project: Path) -> None:
    """A stale file is a stale file; failing loudly is how the operator notices.

    Silently reading it under 3.0 rules produces refusals that look like gate bugs.
    """
    ledger = project / "OLD.md"
    ledger.write_text(
        "## Iteration 1\n\n### F1 — probe\n**Hypothesis.** x\n**Verdict.** CONFIRMED\n",
        encoding="utf-8",
    )
    verdict = gate.decide(ledger=ledger, tool="read", args={"file_path": "src/x.py"})
    assert not verdict.allow


def test_migrated_ledger_is_accepted(gate, project: Path) -> None:
    """A ledger that has hypotheses is work in progress, not stale."""
    ledger = project / "NEW.md"
    ledger.write_text(
        "## Task\nfix\n\nWorking on: H1\n\n### H1 — claim\n| Rank | 1 |\n", encoding="utf-8"
    )
    jrnl.set_phase(ledger, 4)
    assert _decide(gate, ledger).allow


def test_cli_reports_the_legacy_refusal(project: Path) -> None:
    """The adapter path, not just the pure function."""
    ledger = project / "OLD.md"
    ledger.write_text("## Iteration 1\n\n### F1 — baseline\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_GATE), "--ledger", str(ledger)],
        input=json.dumps({"tool_name": "write", "tool_input": {"file_path": "src/x.py"}}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["permission"] == "deny"
    assert "2.1" in payload["agent_message"]
