"""Ledger readers for bug-fixer 3.0 (TASK-081 ST-1).

The v3 ledger is a tree, not a list: hypotheses (``### H<n>``) own records
(``### F<n>``), and a hypothesis status is derived from its record chain rather
than read from an editable field.

Two properties these tests exist for, both learned from the 2.1 → 3.0 rewrite:

* The canonical example in the spec is parsed **from the spec file**, not from a
  copy here. A copy would drift and the test would keep passing while the
  documentation taught a format the gate cannot read.
* Markers are whole lines. The table form (``| Attempt | 3 consumed |``) is a
  format error with a named refusal, because a gate silent on the documented form
  and blind to the written one is the exact failure the skill forbids.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_GATE = jrnl.HOOKS / "bugfixer_gate.py"
#: The spec's §14 ledger, kept inside the module: the plugin must be testable without
#: the parent repository. A parent-side test asserts this fixture equals the spec.
_CANONICAL = jrnl.SKILL_DIR / "tests" / "fixtures" / "canonical-ledger.md"


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
    return _load("bugfixer_gate_ledger_v3", _GATE)


@pytest.fixture(scope="module")
def canonical(gate) -> str:
    """The canonical ledger from spec §14, kept as a fixture inside the plugin.

    The plugin must be testable on a machine that has never seen the parent repository, so
    the example travels with it. A parent-side test asserts this fixture is still byte-equal
    to the spec, which is what stops the two copies drifting.
    """
    assert _CANONICAL.is_file(), f"canonical ledger fixture is missing: {_CANONICAL}"
    return _CANONICAL.read_text(encoding="utf-8")


# --- canonical example (invariants 17a, 12, 27) ------------------------------


def test_canonical_ledger_parses_with_every_reader(gate, canonical: str) -> None:
    """Invariant 17a: the documented example is readable by every parser."""
    hypotheses = gate.parse_hypotheses(canonical)
    records = gate.parse_records(canonical)

    assert set(hypotheses) == {"H0", "H1", "H2"}
    assert [record.id for record in records] == ["F1", "F2", "F3", "F4", "F5", "F6"]
    assert gate.count_attempts(canonical) == 3
    assert gate.active_hypothesis(canonical) == "H2"


def test_canonical_ledger_derives_the_documented_statuses(gate, canonical: str) -> None:
    """Derived status must match the story the example tells."""
    statuses = {h.id: h.status for h in gate.parse_hypotheses(canonical).values()}
    assert statuses == {"H0": "REFUTED", "H1": "REFUTED", "H2": "CONFIRMED"}


def test_canonical_ledger_has_exactly_one_active_marker(gate, canonical: str) -> None:
    """Invariant 12: one pointer, rewritten in place — not a switch log."""
    assert len(re.findall(r"^Working on:", canonical, re.MULTILINE)) == 1


def test_canonical_records_carry_kind_and_scope(gate, canonical: str) -> None:
    """``Kind`` distinguishes the phase that wrote a record (invariant 18)."""
    kinds = {record.id: (record.kind, record.scope) for record in gate.parse_records(canonical)}
    assert kinds["F2"] == ("route", "task")
    assert kinds["F4"] == ("research", "task")
    assert kinds["F6"] == ("probe", "H2")


def test_canonical_has_a_probe_record_for_the_proven_hypothesis(gate, canonical: str) -> None:
    """``PROVEN`` needs ``Kind: probe``: a route or research record must not close it."""
    probes = [record for record in gate.parse_records(canonical) if record.kind == "probe"]
    assert {record.scope for record in probes} == {"H0", "H1", "H2"}


# --- attempts ----------------------------------------------------------------


def test_attempts_are_counted(gate) -> None:
    """Each ``Attempt <k> consumed`` is one probe that reached a code change."""
    text = "Attempt 1 consumed\nAttempt 2 consumed\n"
    assert gate.count_attempts(text) == 2


def test_attempt_inside_prose_is_not_a_probe(gate) -> None:
    """A sentence about the marker is not the marker."""
    text = "The gate counts every `Attempt 3 consumed` line it sees.\n"
    assert gate.count_attempts(text) == 0


def test_attempt_in_a_table_row_is_not_a_probe(gate) -> None:
    """The table form is rejected, not silently accepted (invariant 17b)."""
    text = "| Attempt | 3 consumed |\n"
    assert gate.count_attempts(text) == 0
    assert gate.table_form_attempt(text) is True


def test_table_form_recheck_is_reported(gate) -> None:
    """Same rule for ``Recheck``."""
    assert gate.table_form_attempt("| Recheck | H1 #1 |\n") is True


def test_counters_restart_after_an_operator_reset(gate) -> None:
    """A reset makes the numbering start again, so only later attempts count."""
    text = (
        "Attempt 1 consumed\nAttempt 2 consumed\n"
        "Counter reset: A -> 0 (by operator: new baseline after fixture fix)\n"
        "Attempt 1 consumed\n"
    )
    assert gate.count_attempts(text) == 1


def test_agent_written_reset_is_refused(gate) -> None:
    """Only the operator may reset ``A``; otherwise the cap is decorative."""
    text = "Attempt 1 consumed\nCounter reset: A -> 0\n"
    with pytest.raises(gate.CounterResetError):
        gate.count_attempts(text)


def test_reset_without_a_reason_is_refused(gate) -> None:
    """The strict form carries a reason, so a future reader knows why."""
    text = "Attempt 1 consumed\nCounter reset: A -> 0 (by agent)\n"
    with pytest.raises(gate.CounterResetError):
        gate.count_attempts(text)


# --- rechecks ----------------------------------------------------------------


def test_rechecks_are_counted_per_hypothesis(gate) -> None:
    """``R<h>`` is per hypothesis: five re-runs of H1 say nothing about H2."""
    text = "Recheck H1 #1\nRecheck H1 #2\nRecheck H2 #1\n"
    assert gate.count_rechecks(text, "H1") == 2
    assert gate.count_rechecks(text, "H2") == 1
    assert gate.count_rechecks(text, "H3") == 0


# --- hypotheses --------------------------------------------------------------


def test_hypotheses_without_any_block_is_empty_not_an_error(gate) -> None:
    """A fresh ledger has no hypotheses yet."""
    assert gate.parse_hypotheses("## Task\n\nnothing here\n") == {}


def test_rank_and_metadata_are_parsed(gate) -> None:
    """``Rank``, ``Proposed``, ``Origin`` feed the queue order and the report."""
    text = (
        "### H2 — Второй вызывающий сбрасывает чужой ключ\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| Rank | 3 |\n"
        "| Status | derived (не редактируется) |\n"
        "| Proposed | F4 |\n"
        "| Origin | logs+search |\n"
        "| Mechanism | caller B resets key A |\n"
        "| Prediction | isolation removes both symptoms |\n"
        "| Discriminator | against H1: fixes both |\n"
        "\n"
        "#### Records\n"
        "- F4 → PROPOSED\n"
    )
    hypothesis = gate.parse_hypotheses(text)["H2"]
    assert hypothesis.rank == 3
    assert hypothesis.proposed == "F4"
    assert hypothesis.origin == "logs+search"
    assert hypothesis.mechanism == "caller B resets key A"
    assert hypothesis.prediction == "isolation removes both symptoms"
    assert hypothesis.discriminator == "against H1: fixes both"
    assert hypothesis.status == "PROPOSED"


def test_status_is_derived_from_records_not_from_the_field(gate) -> None:
    """Invariant 10: an edited ``Status`` field cannot change the truth."""
    text = (
        "### H1 — claim\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| Rank | 2 |\n"
        "| Status | CONFIRMED |\n"
        "\n"
        "#### Records\n"
        "- F4 → PROPOSED\n"
        "- F7 → REFUTED\n"
    )
    hypothesis = gate.parse_hypotheses(text)["H1"]
    assert hypothesis.status == "REFUTED"
    assert hypothesis.declared_status == "CONFIRMED"


def test_partial_verdict_keeps_the_hypothesis_in_the_queue(gate) -> None:
    """Invariant 27c: a partial hit is deepened, so the hypothesis stays unchecked."""
    text = (
        "### H1 — claim\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| Rank | 2 |\n"
        "\n"
        "#### Records\n"
        "- F4 → PROPOSED\n"
        "- F5 → PARTIALLY (A)\n"
    )
    hypothesis = gate.parse_hypotheses(text)["H1"]
    assert hypothesis.status == "PROPOSED"
    assert [h.id for h in gate.unchecked_hypotheses(text)] == ["H1"]


def test_unchecked_hypotheses_come_in_rank_order(gate) -> None:
    """Step 4.0 takes the next hypothesis by ``Rank``, not by file order."""
    text = (
        "### H3 — third\n| Rank | 4 |\n\n#### Records\n- F4 → PROPOSED\n\n"
        "### H2 — second\n| Rank | 2 |\n\n#### Records\n- F4 → PROPOSED\n\n"
        "### H1 — first and refuted\n| Rank | 1 |\n\n#### Records\n- F4 → REFUTED\n"
    )
    assert [h.id for h in gate.unchecked_hypotheses(text)] == ["H2", "H3"]


def test_hypothesis_count_includes_duplicates(gate) -> None:
    """A second ``### H0`` is a defect, so counting must not deduplicate it away."""
    text = "### H0 — a\n\n### H0 — b\n"
    assert gate.count_hypotheses(text) == 2
    assert list(gate.parse_hypotheses(text)) == ["H0"]


# --- records -----------------------------------------------------------------


def test_records_parse_required_fields(gate) -> None:
    """``Scope``, ``Responds to``, ``Outcome`` are what the report is built from."""
    text = (
        "### F5 — Проба H1: stale упал, дубли нет\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| Kind | probe |\n"
        "| Scope | H1 |\n"
        "| Responds to | F4 |\n"
        "| Supersedes | — |\n"
        "| Iteration | 1 |\n"
        "| Outcome | REFUTED |\n"
    )
    record = gate.parse_records(text)[0]
    assert record.kind == "probe"
    assert record.scope == "H1"
    assert record.responds_to == "F4"
    assert record.outcome == "REFUTED"


def test_record_body_keeps_the_markers(gate) -> None:
    """Markers live in the body, so the body has to survive parsing."""
    text = (
        "### F3 — Проба H0\n"
        "| Kind | probe |\n"
        "ФАКТ (RED): pytest tests/x.py -> 2 failed\n"
    )
    assert "ФАКТ (RED):" in gate.parse_records(text)[0].body


# --- active hypothesis -------------------------------------------------------


def test_no_marker_is_empty_string(gate) -> None:
    """Phase 1 and 2 have no active hypothesis, and that is not an error."""
    assert gate.active_hypothesis("## Task\n") == ""


def test_two_markers_are_ambiguous(gate) -> None:
    """Invariant 12: the marker is rewritten, so two lines mean a broken rewrite."""
    text = "Working on: H1\nWorking on: H2\n"
    with pytest.raises(gate.AmbiguousMarkerError):
        gate.active_hypothesis(text)


def test_marker_with_trailing_noise_does_not_match(gate) -> None:
    """``Working on: H2 (old)`` is not a pointer: failing loudly beats guessing."""
    assert gate.active_hypothesis("Working on: H2 (old)\n") == ""


def test_marker_inside_prose_is_not_a_marker(gate) -> None:
    """The gate reads the marker, not a sentence about it."""
    assert gate.active_hypothesis("Rewrite `Working on: H2` when switching.\n") == ""


# --- the sub-task status table (§5.3a) ---------------------------------------

TABLE = (
    "| № | Подзадача | Файл | Статус |\n"
    "|---|---|---|---|\n"
    "| ST-1 | read the ledger | [`ST-1.md`](TASK-999/ST-1.md) | ✅ CONFIRMED |\n"
    "| ST-2 | retry cost | [`ST-2.md`](TASK-999/ST-2.md) | ⚠️ частично: нет данных |\n"
    "| ST-3 | phase map | [`ST-3.md`](TASK-999/ST-3.md) | ⬜ открыта |\n"
    "| ST-4 | adjacent branch | [`ST-4.md`](TASK-999/ST-4.md) | ⏭ ST-1 закрыла задачу |\n"
)


def test_subtasks_are_read_from_the_ledger_table(gate) -> None:
    """The table is the only machine-readable place: sub-files are not opened for this.

    Measured on TASK-086: its 14 sub-tasks exist only as rows, and the gate saw none of them,
    because it looked for `### H<n>` blocks that the ledger never had.
    """
    rows = gate.parse_subtasks(TABLE)
    assert [row.id for row in rows] == ["ST-1", "ST-2", "ST-3", "ST-4"]
    assert [row.marker for row in rows] == ["✅", "⚠️", "⬜", "⏭"]
    assert rows[0].text == "CONFIRMED"


def test_a_closed_marker_means_closed(gate) -> None:
    """`✅` and `⏭` mean "nothing further is needed"; `⚠️` and `⬜` do not.

    A skip counts as closed because the whole point of `⏭` is that another sub-task did the work;
    a partial one does not, because its remainder is still open.
    """
    closed = {row.id for row in gate.parse_subtasks(TABLE) if row.closed}
    assert closed == {"ST-1", "ST-4"}


def test_a_marker_outside_the_set_is_refused(gate) -> None:
    """A status word the table never defines leaves the row meaningless to a reader and a gate."""
    text = TABLE.replace("✅ CONFIRMED", "done")
    problems = gate.subtask_status_problems(text)
    assert problems, "an undefined marker must be reported"
    assert "ST-1" in problems[0]


def test_a_skip_without_a_reason_is_refused(gate) -> None:
    """`⏭` is a decision only when it says what made the sub-task unnecessary."""
    text = TABLE.replace("⏭ ST-1 закрыла задачу", "⏭")
    problems = gate.subtask_status_problems(text)
    assert problems
    assert "ST-4" in problems[0]
    assert "reason" in problems[0]


def test_a_complete_table_has_no_problems(gate) -> None:
    """The control: a well-formed table must not be refused, or the rule is noise."""
    assert gate.subtask_status_problems(TABLE) == []


def test_a_ledger_without_the_table_is_not_an_error(gate) -> None:
    """An older task has no table; parsing it must return nothing rather than invent rows."""
    assert gate.parse_subtasks("## Task\nfix\n") == []
    assert gate.subtask_status_problems("## Task\nfix\n") == []


def test_the_header_rule_is_not_a_row(gate) -> None:
    """`|---|---|---|---|` matches the row pattern and carries no status; it must be skipped.

    Without the guard the separator becomes a sub-task whose id is the literal `ST-`-less rule,
    and the very first cell of every table is reported as unreadable.
    """
    text = (
        "| № | Подзадача | Файл | Статус |\n"
        "|---|---|---|---|\n"
        "| ST-1 | read | [`ST-1.md`](x) | ✅ done |\n"
    )
    rows = gate.parse_subtasks(text)
    assert [row.id for row in rows] == ["ST-1"]


def test_a_row_missing_its_status_is_skipped(gate) -> None:
    """A row cut short carries no marker; inventing one would report a state that was not written."""
    text = (
        "| № | Подзадача | Файл | Статус |\n"
        "|---|---|---|---|\n"
        "| ST-1 | read | [`ST-1.md`](x) |  |\n"
    )
    assert gate.parse_subtasks(text) == []
