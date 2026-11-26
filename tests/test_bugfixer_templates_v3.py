"""Templates and reference examples for bug-fixer 3.0 (TASK-081 ST-6).

The point of these tests is drift, not prose quality. A template is copied by an
agent and a reference example is copied by a person; if either shows a format the
gate cannot read, the gate is silent for exactly the case the documentation
teaches. So the example inside ``references/workflow.md`` is parsed by the same
readers the gate uses, not by a regex written for the test.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_SKILL_DIR = jrnl.SKILL_DIR
_GATE = _SKILL_DIR / "hooks" / "bugfixer_gate.py"
_TEMPLATE_FINDING = _SKILL_DIR / "templates" / "finding-block.md"
_TEMPLATE_HYPOTHESIS = _SKILL_DIR / "templates" / "hypothesis-block.md"
_WORKFLOW = _SKILL_DIR / "references" / "workflow.md"


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
    """The gate module, for its ledger readers."""
    return _load("bugfixer_gate_templates", _GATE)


def _fenced_blocks(text: str) -> list[str]:
    """Return every fenced block body, in order.

    Args:
        text: Markdown file contents.

    Returns:
        Block bodies without the fence lines.
    """
    return re.findall(r"```(?:markdown)?\n(.*?)```", text, re.DOTALL)


# --- workflow.md examples are parsed by the real readers ---------------------


def test_workflow_example_parses_with_the_ledger_readers(gate) -> None:
    """A documented example the gate cannot read teaches a format that fails."""
    blocks = _fenced_blocks(_WORKFLOW.read_text(encoding="utf-8"))
    ledgers = [block for block in blocks if "### H2 —" in block]
    assert ledgers, "workflow.md must contain a worked ledger fragment"

    ledger = ledgers[0]
    hypotheses = gate.parse_hypotheses(ledger)
    records = gate.parse_records(ledger)

    assert "H2" in hypotheses, "the worked example's hypothesis is not parsed"
    assert hypotheses["H2"].status == "CONFIRMED", (
        "the example's Records list must derive CONFIRMED, or it teaches a status "
        "the gate would compute differently"
    )
    assert [record.id for record in records] == ["F6"]
    assert records[0].kind == "probe"
    assert records[0].scope == "H2"
    assert gate.active_hypothesis(ledger) == "H2"


def test_workflow_example_satisfies_the_proven_contract(gate) -> None:
    """The example claims CONFIRMED, so it must carry the proofs ``PROVEN`` reads."""
    blocks = _fenced_blocks(_WORKFLOW.read_text(encoding="utf-8"))
    ledger = next(block for block in blocks if "### H2 —" in block)
    body = next(record.body for record in gate.parse_records(ledger) if record.id == "F6")

    for marker in ("ФАКТ (RED):", "ФАКТ (GREEN):", "ФАКТ (логи):", "Bug: stable",
                   "Attempt", "Secrets scan:", "FAILING TEST:"):
        assert marker in body, (
            f"the example's CONFIRMED probe lacks {marker!r}; it would be refused by PROVEN"
        )


def test_workflow_results_table_has_the_required_columns(gate) -> None:
    """'Changed' is not 'better': the columns are what make the numbers checkable."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    for column in ("До", "После", "Порог", "Вывод"):
        assert column in text, f"results-table examples never show the {column!r} column"
    assert "без изменений" in text, (
        "the unchanged example matters more than the fixed one: 120→118 above a met "
        "threshold proves nothing"
    )


def test_workflow_names_the_resolution_rule() -> None:
    """Two metrics moving in opposite ways is a choice, and the choice must be named."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "Resolution:" in text
    assert "не автоматизируется" in text or "человек" in text.lower(), (
        "the limit must be stated: the gate checks presence, a human reads the reasoning"
    )


def test_workflow_does_not_teach_the_three_phase_cycle() -> None:
    """Two automata in the reference is the same defect as two in SKILL.md.

    Naming a phase number is fine — ``Phase 2 (route)`` is a cross-reference. What
    must not appear is a *marker of the old scheme*: an edge that no longer exists,
    the ``M`` counter, or "Phase 3 = decide".
    """
    text = _WORKFLOW.read_text(encoding="utf-8")
    for stale in ("BACK_TO_PHASE_1", "**Verdict.**", "`M` (эксперименты)", "Phase 3 — DECIDE"):
        assert stale not in text, f"workflow.md still references the 2.1 scheme: {stale!r}"
    # The two phase names that changed meaning are the ones worth pinning.
    assert "HYPOTHESIZE & PROBE" not in text


# --- templates ---------------------------------------------------------------


def test_finding_template_parses_as_a_record(gate) -> None:
    """Every fenced block in the finding template must be readable by the parser."""
    blocks = _fenced_blocks(_TEMPLATE_FINDING.read_text(encoding="utf-8"))
    record_blocks = [block for block in blocks if "### F" in block]
    assert record_blocks, "the finding template has no record example"

    parsed = [record for block in record_blocks for record in gate.parse_records(block)]
    assert parsed, "no record parsed out of the template"
    for record in parsed:
        assert record.kind in {"route", "research", "probe"}, (
            f"{record.id} has no usable Kind, so PROVEN could not tell a probe from a route"
        )


def test_hypothesis_template_parses_as_a_hypothesis(gate) -> None:
    """The hypothesis template must produce a block the reader accepts."""
    blocks = _fenced_blocks(_TEMPLATE_HYPOTHESIS.read_text(encoding="utf-8"))
    hypothesis_blocks = [block for block in blocks if "### H" in block]
    assert hypothesis_blocks, "the hypothesis template has no example"

    parsed = [gate.parse_hypotheses(block) for block in hypothesis_blocks]
    found = {hid: hyp for mapping in parsed for hid, hyp in mapping.items()}
    assert found, "no hypothesis parsed out of the template"
    for hypothesis in found.values():
        assert hypothesis.rank > 0, f"{hypothesis.id} shows an example without a usable Rank"
        assert hypothesis.mechanism, f"{hypothesis.id} example lacks Mechanism"
        assert hypothesis.prediction, f"{hypothesis.id} example lacks Prediction"


def test_templates_use_the_long_dash_in_headings(gate) -> None:
    """A short hyphen is not a heading: the parser would see no block at all."""
    for path in (_TEMPLATE_FINDING, _TEMPLATE_HYPOTHESIS, _WORKFLOW):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.match(r"^###\s+[HF]\d+\s*[-–—]", line):
                assert "—" in line, (
                    f"{path.name}: heading uses a dash the parser does not accept: {line!r}"
                )
