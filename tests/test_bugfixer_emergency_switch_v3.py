"""The emergency switch: `Phase gate: off` (TASK-085 Б-6, DoD-14).

The switch existed in the gate from the start and was documented nowhere. Measured consequence,
twice in one session: whoever touches the plugin's own code with the plugin installed locks
themselves out — the gate refuses the edit, and the documented way out is the flag `--dev-plugin`,
which the harness config did not pass.

The switch is deliberately narrow: it turns off the **phase** rule for one ledger and nothing
else. A test that only checks "off lets things through" would pass against a switch that disables
the whole gate, so every case below has a neighbour that must still refuse.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_HOOKS = jrnl.HOOKS
_GATE = _HOOKS / "bugfixer_gate.py"

CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)

LEDGER = (
    "## Task\nfix\n\n## Clarifications\nQ: a  A: b\nQ: c  A: d\nQ: e  A: f\n\n"
    "FAILING TEST: pytest -> 1 failed\nBASELINE: 5/5\n"
)


@pytest.fixture()
def gate():
    """The gate module, imported as the plugin ships it."""
    if str(_HOOKS) not in sys.path:
        sys.path.insert(0, str(_HOOKS))
    return importlib.import_module("bugfixer_gate")


def _project(root: Path, extra: str = "") -> Path:
    """A project in phase 1, optionally carrying the switch."""
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    ledger = root / "tasks" / "TASK-1.md"
    ledger.write_text(LEDGER + extra, encoding="utf-8")
    jrnl.set_phase(ledger, 1)
    return ledger


def test_the_switch_turns_off_the_phase_rule_for_this_ledger(gate, tmp_path: Path) -> None:
    """Measured: `lib/core.py` is refused without the line and allowed with it."""
    plain = _project(tmp_path)
    assert not gate.decide(ledger=plain, tool="write", args={"file_path": "lib/core.py"}).allow

    switched = _project(tmp_path / "switched", "\nPhase gate: off\n")
    verdict = gate.decide(ledger=switched, tool="write", args={"file_path": "lib/core.py"})
    assert verdict.allow, verdict.reason


def test_the_switch_does_not_disable_the_gate(gate, tmp_path: Path) -> None:
    """A terminal status still stops every write: the switch targets one rule, not the guard.

    Without this neighbour, a switch implemented as "return allow at the top of `_decide`" would
    pass the test above and protect nothing.
    """
    ledger = _project(tmp_path, "\nPhase gate: off\n")
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + "\nSTATUS: DONE\n", encoding="utf-8"
    )
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "lib/core.py"})
    assert not verdict.allow


def test_the_switch_must_be_a_whole_line(gate, tmp_path: Path) -> None:
    """Prose that mentions the switch must not satisfy it — the same rule as every marker.

    A ledger describing the emergency exit is not a ledger using it. This is the defect class the
    spec names for `STATUS: BLOCKED` in prose (invariant 17b), and it applies here too.
    """
    ledger = _project(tmp_path, "\nTooling ledgers record `Phase gate: off` on their own line.\n")
    assert not gate.decide(ledger=ledger, tool="write", args={"file_path": "lib/core.py"}).allow


def test_the_switch_is_case_insensitive(gate, tmp_path: Path) -> None:
    """An operator typing `phase gate: OFF` in a hurry means the same thing."""
    ledger = _project(tmp_path, "\nphase gate: OFF\n")
    assert gate.decide(ledger=ledger, tool="write", args={"file_path": "lib/core.py"}).allow


def test_the_documentation_explains_the_switch_and_its_two_ways_out() -> None:
    """Б-6: a switch nobody documents is a switch nobody can use.

    Both exits are required, because they are for different people: `--dev-plugin` for someone
    developing the plugin with its own gate installed, `Phase gate: off` for a ledger that is not
    a bug-fix run at all. The docs must also say what the switch does **not** turn off, or it
    reads as "disables the gate".
    """
    skill = (jrnl.SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    readme = (jrnl.SKILL_DIR / "README.md").read_text(encoding="utf-8")
    for name, text in (("SKILL.md", skill), ("README.md", readme)):
        assert "Phase gate: off" in text, f"{name} does not document the emergency switch"
        assert "--dev-plugin" in text, f"{name} does not document the dev flag"
        assert "Phase gate: off" in text[text.index("Phase gate: off") :], name
    # What it does not cover has to be stated next to the switch, or the reader assumes "all".
    section = skill[skill.index("Phase gate: off") :]
    assert "не выключает" in section or "does not" in section, (
        "SKILL.md does not say what the switch leaves in force"
    )


def test_the_switch_is_not_a_default_anywhere() -> None:
    """The rule must stay on unless a ledger opts out: an opt-out that ships enabled protects nothing."""
    readme = (jrnl.SKILL_DIR / "README.md").read_text(encoding="utf-8")
    lowered = readme.lower()
    for phrase in ("phase gate: off by default", "gate is off by default"):
        assert phrase not in lowered, f"README implies the gate ships disabled: {phrase!r}"
