"""State machine for bug-fixer 3.0 (TASK-081 ST-2).

Eight **named** edges, five phases. The names are not decoration: two different
reasons lead from probe back to research — ``QUEUE_EXHAUSTED`` (refuted, queue
empty) and ``DEEPEN_SAME`` (partially right, deepen this one). Under the 2.1
``dict[(from, to)]`` only one of them could exist and the other silently vanished;
the whole point of the rewrite is that this is now impossible.

Two properties under test throughout:

* a refusal names the marker to write — "illegal transition" teaches an agent
  nothing and gets bypassed;
* the ledger in these tests is written the way the skill tells the agent to write
  it: one ``Working on:`` line, rewritten in place, and the verdict appended to the
  *hypothesis's* ``#### Records``. A fixture that keeps two markers would test a
  ledger the gate must reject.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_HOOKS = jrnl.HOOKS
_STATE = _HOOKS / "bugfixer_gate_state.py"
_GATE = _HOOKS / "bugfixer_gate.py"


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
def state_module():
    """The state-machine module."""
    return _load("bugfixer_state_v3", _STATE)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the state CLI."""
    return subprocess.run(
        [sys.executable, str(_STATE), *args],
        text=True,
        capture_output=True,
        check=False,
    )


# --- ledger builders ---------------------------------------------------------

ROUTE = (
    "\n### F2 — route\n"
    "| Field | Value |\n"
    "|---|---|\n"
    "| Kind | route |\n"
    "| Scope | task |\n"
    "| Outcome | ROUTED |\n"
)

RESEARCH_RECORD = (
    "\n### F4 — research\n"
    "| Field | Value |\n"
    "|---|---|\n"
    "| Kind | research |\n"
    "| Scope | task |\n"
    "| Outcome | RESEARCH |\n"
    "\n"
    "Merge: none — predictions differ\n"
)


def _hypothesis(hid: str, rank: int, records: list[tuple[str, str]], proposed: str = "F2") -> str:
    """Render one ``### H<n>`` block.

    Args:
        hid: Hypothesis id.
        rank: Probe order.
        records: ``(record id, verdict)`` pairs, oldest first.
        proposed: Record that introduced the hypothesis.

    Returns:
        The block as markdown.
    """
    lines = "".join(f"- {record_id} → {verdict}\n" for record_id, verdict in records)
    return (
        f"\n### {hid} — утверждение {hid}\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Rank | {rank} |\n"
        "| Status | derived |\n"
        f"| Proposed | {proposed} |\n"
        "| Origin | description |\n"
        "| Mechanism | механизм |\n"
        "| Prediction | предсказание |\n"
        "| Discriminator | отличие |\n"
        "\n"
        "#### Records\n"
        f"{lines}"
    )


def _probe(fid: str, hid: str, outcome: str, facts: str = "") -> str:
    """Render one ``### F<n>`` probe record.

    Args:
        fid: Record id.
        hid: Hypothesis probed.
        outcome: ``Outcome`` value.
        facts: Marker lines for the body.

    Returns:
        The block as markdown.
    """
    return (
        f"\n### {fid} — проба {hid}\n"
        "| Field | Value |\n"
        "|---|---|\n"
        "| Kind | probe |\n"
        f"| Scope | {hid} |\n"
        f"| Outcome | {outcome} |\n"
        "\n"
        f"{facts}"
    )


BASELINE = (
    "## Task\n\n"
    "## Clarifications\n"
    "Q: На каком входе воспроизводится?  A: пустой id\n"
    "Q: Как выглядит правильное поведение?  A: 404\n"
    "Q: Каким тестом проверите?  A: test_missing_id\n"
    "\n"
    "FAILING TEST: pytest tests/test_users.py -> 1 failed\n"
    "BASELINE: 1652/1654, красные: test_old (issue #12)\n"
)

CONFIRMED_FACTS = (
    "FAILING TEST: pytest tests/test_a.py -> failed\n"
    "FAILING TEST: pytest tests/test_b.py -> failed\n"
    "\n"
    "| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |\n"
    "|---|---|---|---|---|---|---|\n"
    "| test_a | stale | hits | 7 | 0 | 0 | исправлено |\n"
    "\n"
    "ФАКТ (RED): pytest -> 2 failed\n"
    "ФАКТ (логи): invalidate after commit\n"
    "Bug: stable\n"
    "ФАКТ (GREEN): pytest -> 1654 passed\n"
    "Attempt 2 consumed\n"
    "Secrets scan: clean\n"
)

REFUTED_FACTS = (
    "ФАКТ (логи): invalidate уже после commit\n"
    "Bug: stable\n"
    "Revert: src/cache.py\n"
)

PARTIAL_FACTS = (
    "ФАКТ (логи): инвалидация работает, дубли из второго вызывающего\n"
    "Bug: stable\n"
    "ФАКТ (дельта): duplicates baseline 7 -> after 2, expected 0\n"
    "Attempt 2 consumed\n"
    "Secrets scan: clean\n"
    "NEW INFORMATION: второй вызывающий\n"
)


def _write(ledger: Path, text: str, phase: int) -> Path:
    """Write a ledger and put it in the given phase.

    Args:
        ledger: Ledger path.
        text: Full ledger contents.
        phase: Phase to record in the run journal.

    Returns:
        The ledger path.
    """
    ledger.write_text(text, encoding="utf-8")
    jrnl.set_phase(ledger, phase)
    return ledger


def _sidecar(ledger: Path) -> dict:
    """Transitions the state CLI recorded, as it reports them.

    Fixture ``advance`` records carry no ``edge``; a real transition always does (§5.4),
    so filtering on it separates setup from history instead of guessing by position.
    """
    return {
        "transitions": [
            record for record in jrnl.read(ledger)
            if record["event"] == "advance" and "edge" in record
        ]
    }


def _set_phase(ledger: Path, phase: int) -> None:
    """Put the ledger in a phase without going through guards."""
    jrnl.set_phase(ledger, phase)


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    """A ledger ready for ``REPRODUCED`` and nothing further."""
    path = tmp_path / "TASK-999.md"
    path.write_text(BASELINE, encoding="utf-8")
    return path


#: Phase 2: one route record, one complete ``H0``, and the marker on it.
AT_PHASE_2 = (
    BASELINE + ROUTE + "Working on: H0\n" + _hypothesis("H0", 1, [("F2", "PROPOSED")])
)

#: Phase 3, after ``H0`` was refuted and research added ``H1``; ``H1`` is active.
AT_PHASE_3 = (
    BASELINE
    + ROUTE
    + _hypothesis("H0", 1, [("F2", "PROPOSED"), ("F3", "REFUTED")])
    + _probe("F3", "H0", "REFUTED", REFUTED_FACTS + "Attempt 1 consumed\n")
    + RESEARCH_RECORD
    + _hypothesis("H1", 2, [("F4", "PROPOSED")], proposed="F4")
    + "Working on: H1\n"
)


def _phase_4_probe(outcome: str, facts: str, *, extra_h2: bool = False, active: str = "H1") -> str:
    """Build a Phase 4 ledger whose last probe has the given verdict.

    Args:
        outcome: ``Outcome`` value for the probe.
        facts: Marker lines for the probe body.
        extra_h2: Whether a second unchecked hypothesis waits in the queue.
        active: Hypothesis named by ``Working on:``.

    Returns:
        Full ledger text.
    """
    h1_status = {"CONFIRMED": "CONFIRMED", "REFUTED": "REFUTED"}.get(outcome, "PROPOSED")
    parts = [
        BASELINE,
        ROUTE,
        _hypothesis("H0", 1, [("F2", "PROPOSED"), ("F3", "REFUTED")]),
        _probe("F3", "H0", "REFUTED", REFUTED_FACTS + "Attempt 1 consumed\n"),
        RESEARCH_RECORD,
        _hypothesis("H1", 2, [("F4", "PROPOSED"), ("F5", h1_status)], proposed="F4"),
    ]
    if extra_h2:
        parts.append(_hypothesis("H2", 3, [("F4", "PROPOSED")], proposed="F4"))
    parts.append(_probe("F5", "H1", outcome, facts))
    parts.append(f"Working on: {active}\n")
    return "".join(parts)


# --- the eight edges ---------------------------------------------------------


def test_there_are_exactly_eight_named_edges(state_module) -> None:
    """Invariant 1: eight names, keyed by name — not seven phase pairs."""
    assert [edge.name for edge in state_module.EDGES] == [
        "REPRODUCED",
        "ALREADY_TRIED",
        "FIRST_GUESS",
        "HYPOTHESIS_READY",
        "PROVEN",
        "NEXT_IN_QUEUE",
        "QUEUE_EXHAUSTED",
        "DEEPEN_SAME",
    ]
    assert set(state_module.EDGES_BY_NAME) == {edge.name for edge in state_module.EDGES}


def test_both_4_to_3_edges_coexist(state_module) -> None:
    """The defect this rewrite closes: a phase-pair key cannot hold two reasons."""
    assert [edge.name for edge in state_module.edges_between(4, 3)] == [
        "QUEUE_EXHAUSTED",
        "DEEPEN_SAME",
    ]


def test_edge_pair_with_two_meanings_requires_a_name(tmp_path: Path) -> None:
    """Refusing to guess beats picking whichever edge was declared first."""
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", PARTIAL_FACTS), 4)
    result = _run(str(ledger), "advance", "3", "--evidence", "x")
    assert result.returncode == 2
    assert "more than one meaning" in result.stderr
    assert "QUEUE_EXHAUSTED" in result.stderr and "DEEPEN_SAME" in result.stderr


def test_edges_subcommand_lists_all_eight(ledger: Path) -> None:
    """The operator needs the names without reading the source."""
    result = _run(str(ledger), "edges")
    assert result.returncode == 0
    assert len(result.stdout.strip().splitlines()) == 8


def test_unknown_edge_name_for_a_valid_pair_is_refused(ledger: Path) -> None:
    """A name that does not lead there is refused, and the legal ones are listed."""
    result = _run(str(ledger), "advance", "2", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert result.returncode == 2
    assert "REPRODUCED" in result.stderr


# --- REPRODUCED (1 -> 2) -----------------------------------------------------


def test_reproduced_needs_a_red_test(ledger: Path) -> None:
    """A baseline without a reproduction is not enough."""
    ledger.write_text("BASELINE: 10/10\n", encoding="utf-8")
    result = _run(str(ledger), "advance", "2", "--evidence", "x")
    assert result.returncode == 2
    assert "failing test" in result.stderr.lower()


def test_reproduced_needs_a_baseline(ledger: Path) -> None:
    """A reproduction without a baseline cannot tell regression from old defect."""
    ledger.write_text("FAILING TEST: pytest -> 1 failed\n", encoding="utf-8")
    result = _run(str(ledger), "advance", "2", "--evidence", "x")
    assert result.returncode == 2
    assert "BASELINE:" in result.stderr


def test_reproduced_needs_clarification(ledger: Path) -> None:
    """A task understood wrongly yields a correct solution to a different task."""
    ledger.write_text("FAILING TEST: pytest -> 1 failed\nBASELINE: 1/1\n", encoding="utf-8")
    result = _run(str(ledger), "advance", "2", "--evidence", "x")
    assert result.returncode == 2
    assert "not clarified" in result.stderr


def test_three_pairs_of_questions_are_enough(ledger: Path) -> None:
    """Three covers symptom, expectation and verification."""
    assert _run(str(ledger), "advance", "2", "--evidence", "RED + baseline + 3Q").returncode == 0


def test_assumptions_allow_a_headless_run(ledger: Path) -> None:
    """There is no user in headless mode, so stated assumptions replace answers."""
    ledger.write_text(
        "FAILING TEST: pytest -> 1 failed\n"
        "BASELINE: 1/1\n"
        "Assumption: missing id must return 404\n",
        encoding="utf-8",
    )
    assert _run(str(ledger), "advance", "2", "--evidence", "headless").returncode == 0


def test_two_pairs_without_a_gap_line_are_refused(ledger: Path) -> None:
    """Fewer than three answers is allowed only when the gap is named."""
    ledger.write_text(
        "FAILING TEST: pytest -> 1 failed\nBASELINE: 1/1\nQ: a  A: b\nQ: c  A: d\n",
        encoding="utf-8",
    )
    result = _run(str(ledger), "advance", "2", "--evidence", "x")
    assert result.returncode == 2
    assert "Clarification gap:" in result.stderr


def test_two_pairs_with_a_gap_line_are_enough(ledger: Path) -> None:
    """A named gap is an explicit decision, which is what the gate can require."""
    ledger.write_text(
        "FAILING TEST: pytest -> 1 failed\n"
        "BASELINE: 1/1\n"
        "Q: a  A: b\n"
        "Q: c  A: d\n"
        "Clarification gap: unclear which status code — assuming 404\n",
        encoding="utf-8",
    )
    assert _run(str(ledger), "advance", "2", "--evidence", "x").returncode == 0


# --- FIRST_GUESS (2 -> 4) and ALREADY_TRIED (2 -> 3) -------------------------


def test_first_guess_requires_exactly_one_h0(tmp_path: Path) -> None:
    """Phase 2 makes one obvious guess; two is research, none is no progress."""
    ledger = _write(tmp_path / "T.md", BASELINE + ROUTE + "Working on: H0\n", 2)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "exactly one" in result.stderr


def test_first_guess_succeeds_with_a_complete_h0(tmp_path: Path) -> None:
    """The legal path must stay open."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_2, 2)
    result = _run(str(ledger), "advance", "4", "--evidence", "H0 created")
    assert result.returncode == 0, result.stderr
    assert _sidecar(ledger)["transitions"][0]["edge"] == "FIRST_GUESS"


def test_two_h0_blocks_are_refused(tmp_path: Path) -> None:
    """A second ``### H0`` means the cheap attempt is repeated, not extended."""
    text = AT_PHASE_2 + _hypothesis("H0", 2, [("F2", "PROPOSED")])
    ledger = _write(tmp_path / "T.md", text, 2)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "found 2" in result.stderr


def test_first_guess_requires_the_working_on_marker(tmp_path: Path) -> None:
    """Without it the edit cannot be attributed to a hypothesis (invariant 12)."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_2.replace("Working on: H0\n", ""), 2)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "Working on: H0" in result.stderr


def test_first_guess_requires_proposed_to_point_at_a_record(tmp_path: Path) -> None:
    """A hypothesis not tied to the history cannot be reported later."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_2.replace("| Proposed | F2 |", "| Proposed | F9 |"), 2)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "F9" in result.stderr


def test_first_guess_is_refused_when_an_attempt_exists(tmp_path: Path) -> None:
    """An already-spent cheap guess means ``ALREADY_TRIED``, not a fresh one."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_2 + "\nAttempt 1 consumed\n", 2)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "ALREADY_TRIED" in result.stderr


def test_already_tried_needs_a_previous_attempt(tmp_path: Path) -> None:
    """Research before spending the cheap guess is the waste this closes."""
    ledger = _write(tmp_path / "T.md", BASELINE, 2)
    result = _run(str(ledger), "advance", "3", "--evidence", "x")
    assert result.returncode == 2
    assert "no previous attempt" in result.stderr


def test_already_tried_succeeds_after_an_attempt(tmp_path: Path) -> None:
    """A paid, unconfirmed attempt is exactly what research is for."""
    ledger = _write(tmp_path / "T.md", BASELINE + "\nAttempt 1 consumed\n", 2)
    assert _run(str(ledger), "advance", "3", "--evidence", "H0 died").returncode == 0


def test_already_tried_is_refused_when_the_task_is_already_solved(tmp_path: Path) -> None:
    """Researching a closed cause wastes an attempt that cannot help."""
    text = BASELINE + "\nAttempt 1 consumed\n" + _probe("F3", "H0", "CONFIRMED", CONFIRMED_FACTS)
    ledger = _write(tmp_path / "T.md", text, 2)
    result = _run(str(ledger), "advance", "3", "--evidence", "x")
    assert result.returncode == 2
    assert "CONFIRMED" in result.stderr


# --- HYPOTHESIS_READY (3 -> 4) -----------------------------------------------


def test_hypothesis_ready_needs_a_merge_line(tmp_path: Path) -> None:
    """Without the merge note, duplicated hypotheses get probed twice."""
    text = AT_PHASE_3.replace("Merge: none — predictions differ\n", "")
    ledger = _write(tmp_path / "T.md", text, 3)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "Merge:" in result.stderr


def test_hypothesis_ready_needs_an_unchecked_hypothesis(tmp_path: Path) -> None:
    """Research that produced nothing new cannot enter the probe phase."""
    text = AT_PHASE_3.replace(
        _hypothesis("H1", 2, [("F4", "PROPOSED")], proposed="F4"),
        _hypothesis("H1", 2, [("F4", "REFUTED")], proposed="F4"),
    )
    ledger = _write(tmp_path / "T.md", text, 3)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "no unchecked hypothesis" in result.stderr


def test_hypothesis_ready_needs_the_active_marker(tmp_path: Path) -> None:
    """Phase 4 needs to know which hypothesis the edit belongs to (finding D-1)."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_3.replace("Working on: H1\n", ""), 3)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "Working on: H" in result.stderr


def test_hypothesis_ready_refuses_a_marker_on_a_refuted_hypothesis(tmp_path: Path) -> None:
    """Probing a dead hypothesis is not progress."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_3.replace("Working on: H1", "Working on: H0"), 3)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "not an unchecked hypothesis" in result.stderr


def test_hypothesis_ready_refuses_two_markers(tmp_path: Path) -> None:
    """Invariant 12: the marker is rewritten in place, not appended."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_3 + "Working on: H0\n", 3)
    result = _run(str(ledger), "advance", "4", "--evidence", "x")
    assert result.returncode == 2
    assert "more than one `Working on:`" in result.stderr


def test_hypothesis_ready_succeeds(tmp_path: Path) -> None:
    """The legal path must stay open."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_3, 3)
    result = _run(str(ledger), "advance", "4", "--evidence", "H1 ready")
    assert result.returncode == 0, result.stderr
    assert _sidecar(ledger)["transitions"][-1]["edge"] == "HYPOTHESIS_READY"


# --- PROVEN (4 -> 5) ---------------------------------------------------------


def test_proven_succeeds_with_every_proof(tmp_path: Path) -> None:
    """The baseline case: CONFIRMED backed by RED, logs, nature, GREEN, scan, table."""
    ledger = _write(tmp_path / "T.md", _phase_4_probe("CONFIRMED", CONFIRMED_FACTS), 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "cause proven")
    assert result.returncode == 0, result.stderr


def _skipped(text: str, reason: str, hid: str = "H2") -> str:
    """Mark one hypothesis ``SKIPPED`` in a rendered ledger.

    The anchor is taken from what the fixture renders, not from how the source looks: the first
    version of these tests replaced a string that never occurred, `.replace` returned the text
    unchanged, and the case silently became its own control (`probes/st13_fixture_layout.py`).
    The assertion below is what makes the helper honest.

    Args:
        text: Rendered ledger.
        reason: Text after the verdict; an empty string writes a reasonless skip.
        hid: Hypothesis to mark.

    Returns:
        The ledger with the record appended.

    Raises:
        AssertionError: When the hypothesis has no ``#### Records`` list to extend.
    """
    heading = f"### {hid} — утверждение {hid}"
    assert heading in text, f"the fixture does not render {hid}; the case would prove nothing"
    start = text.index(heading)
    marker = "#### Records\n"
    at = text.index(marker, start) + len(marker)
    # APPENDED, not prepended: a hypothesis's status is its LAST record (§5.1), so a skip
    # inserted at the top leaves the earlier verdict in force and the case proves nothing.
    end = at
    for line in text[at:].splitlines(keepends=True):
        if not line.lstrip().startswith("- "):
            break
        end += len(line)
    line = f"- F6 → SKIPPED — {reason}\n" if reason else "- F6 → SKIPPED\n"
    edited = text[:end] + line + text[end:]
    assert edited != text, "the skip line was not inserted"
    return edited


def test_a_skipped_hypothesis_does_not_block_proven(tmp_path: Path) -> None:
    """The goal is met, so an unprobed hypothesis must not hold the run in Phase 4.

    Before this rule the queue had no way to retire a hypothesis that became pointless: it
    stayed `PROPOSED`, and the next session read a finished task as unfinished (§5.1).
    """
    text = _skipped(
        _phase_4_probe("CONFIRMED", CONFIRMED_FACTS, extra_h2=True),
        "H1 confirmed the cause",
    )
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "cause proven")
    assert result.returncode == 0, result.stderr


def test_skipped_without_a_reason_is_refused(tmp_path: Path) -> None:
    """`SKIPPED` alone reads as "forgot", and no reader can tell the two apart."""
    text = _skipped(_phase_4_probe("CONFIRMED", CONFIRMED_FACTS, extra_h2=True), "")
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert "without a reason" in result.stderr
    assert "H2" in result.stderr


def test_skipped_where_nothing_is_confirmed_is_refused(tmp_path: Path) -> None:
    """The bypass this rule must not open: skip everything, call it done.

    `PROVEN` requires a CONFIRMED last probe, so the interesting case is a skip that pretends
    the goal was reached — with the confirming hypothesis absent from the ledger entirely.
    """
    module = _load("bugfixer_gate_state_skipped", _STATE)
    text = (
        BASELINE
        + ROUTE
        + _hypothesis("H0", 1, [("F2", "PROPOSED"), ("F3", "SKIPPED")])
        + _probe("F3", "H0", "REFUTED", REFUTED_FACTS + "Attempt 1 consumed\n")
        + "Working on: H0\n"
    )
    problem = module._skipped_without_goal(text)
    assert problem, "a skip with no CONFIRMED hypothesis must be refused"
    assert "no hypothesis reached" in problem


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("ФАКТ (RED): pytest -> 2 failed\n", "ФАКТ (RED):"),
        ("ФАКТ (логи): invalidate after commit\n", "ФАКТ (логи):"),
        ("Bug: stable\n", "Bug:"),
        ("ФАКТ (GREEN): pytest -> 1654 passed\n", "ФАКТ (GREEN):"),
        ("Secrets scan: clean\n", "Secrets scan:"),
    ],
)
def test_proven_names_each_missing_marker(tmp_path: Path, marker: str, expected: str) -> None:
    """A refusal that does not name the marker is a refusal the agent ignores."""
    facts = CONFIRMED_FACTS.replace(marker, "")
    ledger = _write(tmp_path / "T.md", _phase_4_probe("CONFIRMED", facts), 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert expected in result.stderr


def test_proven_requires_the_results_table(tmp_path: Path) -> None:
    """'It works' without before and after cannot be checked (§11)."""
    table = (
        "| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |\n"
        "|---|---|---|---|---|---|---|\n"
        "| test_a | stale | hits | 7 | 0 | 0 | исправлено |\n"
    )
    facts = CONFIRMED_FACTS.replace(table, "")
    ledger = _write(tmp_path / "T.md", _phase_4_probe("CONFIRMED", facts), 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert "results table" in result.stderr


def test_proven_requires_a_second_test_or_a_named_corner_case(tmp_path: Path) -> None:
    """One test is not a boundary."""
    facts = CONFIRMED_FACTS.replace("FAILING TEST: pytest tests/test_b.py -> failed\n", "")
    ledger = _write(tmp_path / "T.md", _phase_4_probe("CONFIRMED", facts), 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert "Corner cases" in result.stderr


def test_one_test_is_enough_when_the_corner_case_is_named(tmp_path: Path) -> None:
    """An explicit 'there are no edges, because' is a claim a reader can check."""
    facts = CONFIRMED_FACTS.replace(
        "FAILING TEST: pytest tests/test_b.py -> failed\n",
        "Corner cases: none — the input has one field\n",
    )
    ledger = _write(tmp_path / "T.md", _phase_4_probe("CONFIRMED", facts), 4)
    assert _run(str(ledger), "advance", "5", "--evidence", "x").returncode == 0


def test_partially_confirmed_cannot_enter_phase_5(tmp_path: Path) -> None:
    """Invariant 24: a partial hit is deepened, not declared proved."""
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", PARTIAL_FACTS), 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert "DEEPEN_SAME" in result.stderr


def test_proven_requires_an_attempt(tmp_path: Path) -> None:
    """A fixed test is not a fixed bug: the code change must be recorded."""
    facts = CONFIRMED_FACTS.replace("Attempt 2 consumed\n", "")
    ledger = _write(tmp_path / "T.md", _phase_4_probe("CONFIRMED", facts), 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert "Attempt" in result.stderr


def test_proven_requires_a_probe_record_not_a_route(tmp_path: Path) -> None:
    """Invariant 18: a phase-2 record must not close the transition to Phase 5."""
    ledger = _write(tmp_path / "T.md", AT_PHASE_2, 4)
    result = _run(str(ledger), "advance", "5", "--evidence", "x")
    assert result.returncode == 2
    assert "CONFIRMED" in result.stderr


# --- NEXT_IN_QUEUE (4 -> 4) and QUEUE_EXHAUSTED (4 -> 3) ----------------------


def test_next_in_queue_requires_the_marker_to_move_first(tmp_path: Path) -> None:
    """Step 4.9a switches the marker before the edge; 4.0 only confirms it (D-2)."""
    text = _phase_4_probe("REFUTED", REFUTED_FACTS, extra_h2=True, active="H1")
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "4", "--edge", "NEXT_IN_QUEUE", "--evidence", "x")
    assert result.returncode == 2
    assert "Working on: H2" in result.stderr


def test_next_in_queue_succeeds_once_the_marker_is_switched(tmp_path: Path) -> None:
    """The legal path must stay open."""
    text = _phase_4_probe("REFUTED", REFUTED_FACTS, extra_h2=True, active="H2")
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "4", "--edge", "NEXT_IN_QUEUE", "--evidence", "H1 refuted")
    assert result.returncode == 0, result.stderr
    assert _sidecar(ledger)["transitions"][0]["edge"] == "NEXT_IN_QUEUE"


def test_next_in_queue_refuses_a_partial_verdict(tmp_path: Path) -> None:
    """A partial hit is deepened, not abandoned for the next hypothesis."""
    text = _phase_4_probe("PARTIALLY (A)", PARTIAL_FACTS, extra_h2=True, active="H2")
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "4", "--edge", "NEXT_IN_QUEUE", "--evidence", "x")
    assert result.returncode == 2
    assert "REFUTED" in result.stderr


def test_queue_exhausted_refuses_when_a_hypothesis_is_waiting(tmp_path: Path) -> None:
    """Researching a case already formulated repeats paid work."""
    facts = REFUTED_FACTS + "NEW INFORMATION: caller B\n"
    ledger = _write(tmp_path / "T.md", _phase_4_probe("REFUTED", facts, extra_h2=True), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "QUEUE_EXHAUSTED", "--evidence", "x")
    assert result.returncode == 2
    assert "NEXT_IN_QUEUE" in result.stderr


def test_queue_exhausted_needs_new_information(tmp_path: Path) -> None:
    """A return that teaches nothing repeats the same round."""
    ledger = _write(tmp_path / "T.md", _phase_4_probe("REFUTED", REFUTED_FACTS), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "QUEUE_EXHAUSTED", "--evidence", "x")
    assert result.returncode == 2
    assert "NEW INFORMATION:" in result.stderr


def test_queue_exhausted_succeeds_with_an_empty_queue_and_new_information(tmp_path: Path) -> None:
    """The legal path must stay open."""
    facts = REFUTED_FACTS + "NEW INFORMATION: caller B\n"
    ledger = _write(tmp_path / "T.md", _phase_4_probe("REFUTED", facts), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "QUEUE_EXHAUSTED", "--evidence", "x")
    assert result.returncode == 0, result.stderr


def test_refutation_without_logs_is_refused_on_both_edges(tmp_path: Path) -> None:
    """A silent test does not prove the mechanism is absent."""
    facts = REFUTED_FACTS.replace("ФАКТ (логи): invalidate уже после commit\n", "")
    for edge, target in (("QUEUE_EXHAUSTED", "3"), ("NEXT_IN_QUEUE", "4")):
        text = _phase_4_probe("REFUTED", facts + "NEW INFORMATION: x\n", extra_h2=True, active="H2")
        ledger = _write(tmp_path / f"{edge}.md", text, 4)
        result = _run(str(ledger), "advance", target, "--edge", edge, "--evidence", "x")
        assert result.returncode == 2
        assert "ФАКТ (логи):" in result.stderr


def test_refutation_without_a_revert_is_refused(tmp_path: Path) -> None:
    """A rolled-back probe with no line makes the next session repeat it."""
    facts = REFUTED_FACTS.replace("Revert: src/cache.py\n", "") + "NEW INFORMATION: x\n"
    ledger = _write(tmp_path / "T.md", _phase_4_probe("REFUTED", facts), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "QUEUE_EXHAUSTED", "--evidence", "x")
    assert result.returncode == 2
    assert "Revert:" in result.stderr


# --- DEEPEN_SAME (4 -> 3) ----------------------------------------------------


def test_deepen_same_succeeds_with_a_delta(tmp_path: Path) -> None:
    """Numeric progress plus the same hypothesis is the legal path."""
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", PARTIAL_FACTS), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "partial")
    assert result.returncode == 0, result.stderr


def test_deepen_same_needs_three_numbers(tmp_path: Path) -> None:
    """Two numbers do not say what the target was."""
    facts = PARTIAL_FACTS.replace(
        "duplicates baseline 7 -> after 2, expected 0", "7 -> 2"
    )
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", facts), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert result.returncode == 2
    assert "three numbers" in result.stderr


def test_deepen_same_refuses_a_revert_when_a_delta_exists(tmp_path: Path) -> None:
    """Rolling back a probe that moved the numbers throws away what it taught."""
    facts = PARTIAL_FACTS + "Revert: src/cache.py\n"
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", facts), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert result.returncode == 2
    assert "forbidden" in result.stderr


def test_deepen_same_case_c_requires_a_revert(tmp_path: Path) -> None:
    """A regression without progress has bought nothing, so it is rolled back."""
    case_c = PARTIAL_FACTS.replace(
        "ФАКТ (дельта): duplicates baseline 7 -> after 2, expected 0\n",
        "ФАКТ (регрессия): test_orders_sorted\n",
    )
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", case_c), 4)
    refused = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert refused.returncode == 2
    assert "Revert:" in refused.stderr

    ledger.write_text(
        _phase_4_probe("PARTIALLY (A)", case_c + "Revert: src/cache.py\n"), encoding="utf-8"
    )
    allowed = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert allowed.returncode == 0, allowed.stderr


def test_deepen_same_requires_a_resolution_on_a_metrics_conflict(tmp_path: Path) -> None:
    """The agent names the priority; the gate checks that the choice was named."""
    conflict = PARTIAL_FACTS.replace(
        "ФАКТ (дельта): duplicates baseline 7 -> after 2, expected 0\n",
        "ФАКТ (дельта): duplicates 7 -> 2, expected 0\nФАКТ (регрессия): test_p95\n",
    )
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", conflict), 4)
    refused = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert refused.returncode == 2
    assert "Resolution:" in refused.stderr

    ledger.write_text(
        _phase_4_probe(
            "PARTIALLY (A)",
            conflict + "Resolution: latency важнее дублей\n",
        ),
        encoding="utf-8",
    )
    allowed = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert allowed.returncode == 0, allowed.stderr


def test_deepen_same_keeps_the_same_hypothesis_active(tmp_path: Path) -> None:
    """Deepening a different hypothesis silently switches what is being probed."""
    text = _phase_4_probe("PARTIALLY (A)", PARTIAL_FACTS, extra_h2=True, active="H2")
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert result.returncode == 2
    assert "same hypothesis" in result.stderr


def test_deepen_same_ignores_the_queue(tmp_path: Path) -> None:
    """A partial hit is deepened even when another hypothesis is waiting (D-3)."""
    text = _phase_4_probe("PARTIALLY (A)", PARTIAL_FACTS, extra_h2=True, active="H1")
    ledger = _write(tmp_path / "T.md", text, 4)
    result = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert result.returncode == 0, result.stderr


# --- limits ------------------------------------------------------------------


def test_cap_reached_refuses_both_4_to_3_edges(tmp_path: Path) -> None:
    """At ``A = 10`` the only exit is ``LIMIT_REACHED``, not another round."""
    # Attempts 1 and 2 are already in the fixtures; the rest fill the cap to 10.
    attempts = "".join(f"Attempt {n} consumed\n" for n in range(3, 11))
    facts = PARTIAL_FACTS + attempts
    ledger = _write(tmp_path / "T.md", _phase_4_probe("PARTIALLY (A)", facts), 4)
    result = _run(str(ledger), "advance", "3", "--edge", "DEEPEN_SAME", "--evidence", "x")
    assert result.returncode == 2
    assert "LIMIT_REACHED" in result.stderr


def test_limit_reached_is_a_valid_status(ledger: Path) -> None:
    """The status must exist, or the cap has no terminal to write."""
    assert _run(str(ledger), "status", "LIMIT_REACHED").returncode == 0


def test_unknown_status_is_refused(ledger: Path) -> None:
    """Invariant 15: an unreadable status is denied, not read as RUNNING."""
    assert _run(str(ledger), "status", "PROBABLY_FINE").returncode == 2


def test_phase_nine_is_not_a_phase(ledger: Path) -> None:
    """A five-phase automaton with an open-ended phase number is not an automaton."""
    assert _run(str(ledger), "advance", "9", "--evidence", "x").returncode == 2


# --- illegal edges -----------------------------------------------------------


@pytest.mark.parametrize("target", ["3", "4", "5"])
def test_skipping_from_phase_1_is_refused(ledger: Path, target: str) -> None:
    """Skipping a phase is the defect the automaton exists to prevent."""
    result = _run(str(ledger), "advance", target, "--evidence", "trust me")
    assert result.returncode == 2
    assert "illegal transition 1->" in result.stderr


def test_there_is_no_edge_back_to_phase_1(ledger: Path) -> None:
    """Q-1: a wrong baseline is BLOCKED, not a fresh attempt."""
    for phase in (2, 3, 4, 5):
        _set_phase(ledger, phase)
        result = _run(str(ledger), "advance", "1", "--evidence", "x")
        assert result.returncode == 2
        assert f"illegal transition {phase}->1" in result.stderr


def test_there_is_no_edge_from_phase_5_to_research(ledger: Path) -> None:
    """A regression after CONFIRMED is a contradiction in the proofs, not research."""
    _set_phase(ledger, 5)
    result = _run(str(ledger), "advance", "3", "--evidence", "x")
    assert result.returncode == 2
    assert "illegal transition 5->3" in result.stderr


def test_transition_needs_evidence(ledger: Path) -> None:
    """History without a reason is empty prose."""
    result = _run(str(ledger), "advance", "2")
    assert result.returncode == 2
    assert "evidence" in result.stderr.lower()


def test_history_records_the_edge_name(ledger: Path) -> None:
    """The name is what a later reader needs: the phase pair is ambiguous."""
    _run(str(ledger), "advance", "2", "--evidence", "RED + baseline + 3Q")
    transition = _sidecar(ledger)["transitions"][0]
    assert transition["edge"] == "REPRODUCED"
    # §5.4: the justification is the record's ``why``, so the journal stays one shape.
    assert transition["why"] == "RED + baseline + 3Q"


def test_idempotent_advance_writes_no_history(ledger: Path) -> None:
    """Re-setting the current phase is a no-op, not a transition."""
    _run(str(ledger), "advance", "2", "--evidence", "a")
    before = len(_sidecar(ledger)["transitions"])
    assert _run(str(ledger), "advance", "2", "--evidence", "b").returncode == 0
    assert len(_sidecar(ledger)["transitions"]) == before


# --- markers must be anchored ------------------------------------------------


def test_markers_in_prose_do_not_satisfy_a_guard(state_module) -> None:
    """A ledger that documents the marker must not pass the precondition."""
    prose = "The ledger must contain a line `FAILING TEST:` and one `BASELINE:`.\n"
    assert state_module.count_markers(state_module._RED_TEST_RE, prose) == 0
    assert state_module.count_markers(state_module._BASELINE_RE, prose) == 0


def test_clarification_in_prose_does_not_count(state_module) -> None:
    """Otherwise a ledger describing the questions would pass its own gate."""
    prose = "Ask three `Q:`/`A:` pairs and record them under `## Clarifications`.\n"
    assert state_module.count_markers(state_module._CLARIFICATION_Q_RE, prose) == 0


# --- the gate's opt-out for tooling ledgers ----------------------------------


def _gate(payload: dict, ledger: Path) -> dict:
    """Run the gate and return its JSON verdict."""
    result = subprocess.run(
        [sys.executable, str(_GATE), "--ledger", str(ledger)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.stdout.strip(), result.stderr
    return json.loads(result.stdout)


def test_phase_gate_off_allows_tooling_ledgers(tmp_path: Path) -> None:
    """A tooling task cannot be locked out of its own source files."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"

    ledger.write_text("## Iteration 1\n", encoding="utf-8")
    on = _gate({"tool_name": "write", "tool_input": {"file_path": "src/a.py"}}, ledger)
    assert on["permission"] == "deny"

    ledger.write_text("## Iteration 1\n\nPhase gate: off\n", encoding="utf-8")
    off = _gate({"tool_name": "write", "tool_input": {"file_path": "src/a.py"}}, ledger)
    assert off["permission"] == "allow"


def test_phase_gate_off_does_not_unlock_a_terminal_status(tmp_path: Path) -> None:
    """The opt-out covers Phase 1 only; BLOCKED and DONE still stop everything."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text("## Iteration 1\nPhase gate: off\nSTATUS: BLOCKED\n", encoding="utf-8")
    verdict = _gate({"tool_name": "write", "tool_input": {"file_path": "src/a.py"}}, ledger)
    assert verdict["permission"] == "deny"


def test_phase_gate_off_in_prose_is_not_an_opt_out(tmp_path: Path) -> None:
    """Describing the marker must not disable the ban."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text(
        "## Iteration 1\nTooling ledgers record `Phase gate: off` on their own line.\n",
        encoding="utf-8",
    )
    verdict = _gate({"tool_name": "write", "tool_input": {"file_path": "src/a.py"}}, ledger)
    assert verdict["permission"] == "deny"
