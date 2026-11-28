"""A probe whose number enters a decision must be able to come out wrong (TASK-086 ST-17).

Spec §0 makes the rule for tests: a test that cannot fail proves nothing. A probe is worse placed
than a test — nothing re-runs it, and its number is copied into a decision — so the same rule is
applied here, with the two defences that actually caught this session's four wrong numbers:

* a **control with a known answer** (positive: the instrument must see what is there; negative: it
  must not see what is not; control: what is not a marker must be ignored);
* a **known-good range**, measured before the run, so a gross error stops the run instead of
  being printed;
* where a second derivation exists, **two paths agreeing** — broken the same way twice is a much
  smaller set than broken once.

`Discriminator` is deliberately not part of this: it separates a hypothesis from its neighbour
(§5.1, ≥2 hypotheses), and a measurement has no neighbour.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_SKILL = helpers.SKILL_DIR
_LIB = _SKILL / "lib"
_PROBES = _SKILL / "probes"
_STATE = helpers.HOOKS / "bugfixer_gate_state.py"
_SKILL_MD = _SKILL / "SKILL.md"

if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from bugfixer import measurement  # noqa: E402


def _load(name: str, path: Path):
    """Import a module from a file path.

    Args:
        name: Module name to register.
        path: File to import.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def _controlled() -> measurement.Measurement:
    """A measurement whose three controls all hold.

    Returns:
        The measurement, with a plausible range of 7..7.
    """
    taken = measurement.Measurement("a subject with known answers")
    taken.expect(measurement.POSITIVE, "sees what is there", expected={"x"}, got={"x"})
    taken.expect(measurement.NEGATIVE, "misses what is absent", expected=set(), got=set())
    taken.expect(measurement.CONTROL, "ignores a non-marker", expected=[], got=[])
    taken.count(7, plausible=(7, 7))
    return taken


# --- the instrument must be able to fail -------------------------------------


def test_a_number_with_three_good_controls_stands() -> None:
    """The baseline: the helper must not refuse a measurement that is sound.

    Without this, every refusal test below could pass because the helper refuses everything.
    """
    assert _controlled().problems() == []
    assert _controlled().finish() == 0


def test_a_positive_control_that_sees_nothing_refuses() -> None:
    """The defect that produced four wrong numbers: the instrument was blind and said zero."""
    taken = _controlled()
    taken.cases[0] = measurement.Case(
        kind=measurement.POSITIVE,
        label="sees what is there",
        held=False,
        detail="expected {'x'}, got set()",
    )
    with pytest.raises(measurement.Unmeasurable) as refusal:
        taken.finish()
    assert "positive" in str(refusal.value)


def test_a_negative_control_that_sees_a_ghost_refuses() -> None:
    """The other half: an instrument that invents findings is as useless as a blind one."""
    taken = _controlled()
    taken.expect(measurement.NEGATIVE, "misses what is absent", expected=set(), got={"ghost"})
    assert any("negative" in problem for problem in taken.problems())


def test_a_missing_control_refuses_rather_than_being_assumed() -> None:
    """Two cases out of three is not a control set: the one left out is the one that would catch it."""
    bare = measurement.Measurement("no controls at all")
    bare.count(7, plausible=(7, 7))
    problems = bare.problems()
    for kind in measurement.REQUIRED_KINDS:
        assert any(kind in problem for problem in problems), (
            f"a measurement with no {kind} case was accepted: {problems}"
        )


def test_a_number_outside_its_known_range_refuses() -> None:
    """`0` where the repository has `7` must stop the run, not be reported (ST-17 §3)."""
    impossible = _controlled()
    impossible.count(0, plausible=(7, 7))
    assert any("known-good" in problem for problem in impossible.problems())
    with pytest.raises(measurement.Unmeasurable):
        impossible.finish()


def test_a_number_without_a_range_is_not_a_measurement() -> None:
    """A number with no impossible value beside it cannot be told apart from a gross error."""
    unanchored = _controlled()
    unanchored.plausible = None
    assert any("no known-good range" in problem for problem in unanchored.problems())


def test_two_derivations_that_disagree_refuse() -> None:
    """The marker audit was settled by two paths agreeing; disagreement must not pass."""
    split = _controlled()
    split.agree("markers from the AST and from the regex", first=7, second=6)
    assert any("agreement" in problem for problem in split.problems())


def test_the_report_names_the_subject_beside_the_number() -> None:
    """A figure whose subject is unnamed is how the audit measured its own parser."""
    report = _controlled().report()
    assert "a subject with known answers" in report
    assert "value  : 7" in report
    assert "known-good 7..7" in report


def test_a_measurement_without_a_subject_is_refused_at_construction() -> None:
    """An empty subject would make every report ambiguous, so it cannot be built."""
    with pytest.raises(measurement.Unmeasurable):
        measurement.Measurement("   ")


def test_a_probe_using_the_helper_exits_non_zero_on_an_impossible_number(tmp_path: Path) -> None:
    """The claim is about exit codes, not about an exception name: a probe must fail when wrong.

    The script is a real file run in a real interpreter, because `finish()` raising is a property
    of a function call, while `SystemExit` is a property of the process the operator runs.
    """
    script = tmp_path / "impossible_probe.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_LIB)!r})\n"
        "from bugfixer import measurement\n"
        "taken = measurement.Measurement('seeded impossible number')\n"
        "taken.expect(measurement.POSITIVE, 'sees', expected=1, got=1)\n"
        "taken.expect(measurement.NEGATIVE, 'misses', expected=0, got=0)\n"
        "taken.expect(measurement.CONTROL, 'ignores', expected='', got='')\n"
        "taken.count(0, plausible=(7, 7))\n"
        "raise SystemExit(taken.finish())\n",
        encoding="utf-8",
    )
    finished = subprocess.run(
        [sys.executable, str(script)], text=True, capture_output=True, check=False
    )
    assert finished.returncode != 0, f"the probe reported a fiction as a number: {finished.stdout}"
    assert "known-good" in finished.stderr, finished.stderr


# --- the stand is asserted, not assumed --------------------------------------


def test_a_stand_inside_the_plugin_is_refused() -> None:
    """F17: two "defects" were the stand, not the code. A stand in a scanned tree measures itself."""
    with pytest.raises(measurement.Unmeasurable) as refusal:
        measurement.assert_outside(_SKILL / "logs" / "stand")
    assert "inside" in str(refusal.value)


def test_a_stand_inside_the_repository_is_refused_too() -> None:
    """The plugin is a submodule: the enclosing project is scanned as well."""
    repository = measurement.repository_root()
    if repository == measurement.plugin_root():
        pytest.skip("the plugin is its own repository here; nothing encloses it")
    with pytest.raises(measurement.Unmeasurable):
        measurement.assert_outside(repository / "probes" / "stand")


def test_the_stand_helper_creates_a_project_outside_every_scanned_tree() -> None:
    """The documented way to build a stand must satisfy the rule it documents."""
    stand = measurement.make_stand(prefix="st17-test-")
    try:
        assert not any(root in stand.parents or root == stand for root in measurement.forbidden_roots())
    finally:
        for leftover in sorted(stand.rglob("*"), reverse=True):
            leftover.unlink() if leftover.is_file() else leftover.rmdir()
        stand.rmdir()


def test_a_temporary_directory_inside_the_plugin_is_not_accepted_as_a_stand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mkdtemp()` is not enough on its own: where it lands decides whether the stand is a stand."""
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(_SKILL / "logs"))
    with pytest.raises(measurement.Unmeasurable):
        measurement.make_stand()


# --- the probes that carry a decision ---------------------------------------


_USES_HELPER = re.compile(r"from bugfixer import measurement|import measurement\b")
_FINISHES = re.compile(r"\bmeasurement\.(?:Measurement|make_stand)\(")


def _probes_unfinished(directory: Path) -> list[str]:
    """Probe files that take the helper and never let it refuse.

    Args:
        directory: Directory to scan.

    Returns:
        File names using the helper without `finish()`, `report()` or a controlled object.
    """
    unfinished: list[str] = []
    for path in sorted(directory.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if not _USES_HELPER.search(text):
            continue
        if _FINISHES.search(text) and ".finish()" in text:
            continue
        unfinished.append(path.name)
    return unfinished


def test_every_probe_that_takes_the_helper_lets_it_refuse(tmp_path: Path) -> None:
    """An imported helper that is never asked to refuse is decoration, not a control.

    The rule's scope is narrower than "all 110 probes": a probe written before the rule is
    history, and rewriting history buys nothing. What is checked is that a probe which has
    adopted the helper actually uses it as a gate — otherwise the next author copies the import
    and keeps printing.
    """
    assert not _probes_unfinished(_PROBES), (
        "these probes import the measurement helper and never call finish(): "
        f"{_probes_unfinished(_PROBES)}"
    )

    # The scan must be able to see the defect it exists for: a synthetic probe that imports the
    # helper and prints, and one that finishes properly. Without this pair the scan could be
    # matching nothing and still be green.
    seeded = tmp_path / "seeded"
    seeded.mkdir()
    (seeded / "prints_anyway.py").write_text(
        "from bugfixer import measurement\nprint('value: 0')\n", encoding="utf-8"
    )
    assert _probes_unfinished(seeded) == ["prints_anyway.py"]

    (seeded / "prints_anyway.py").write_text(
        "from bugfixer import measurement\n"
        "taken = measurement.Measurement('subject')\n"
        "raise SystemExit(taken.finish())\n",
        encoding="utf-8",
    )
    (seeded / "does_nothing.py").write_text("print('hello')\n", encoding="utf-8")
    assert _probes_unfinished(seeded) == []


# --- the marker audit, as the worked case -----------------------------------


def test_the_marker_audit_agrees_on_both_paths() -> None:
    """DoD-7: the audit is the worked case, and it becomes the regression for the rule.

    Two derivations — an AST walk over `re.compile` arguments and a line regex over string
    literals — must produce the same seven markers, and both must agree with the published list.
    """
    language = _load("test_bugfixer_language_v3_st17", _SKILL / "tests" / "test_bugfixer_language_v3.py")
    ast_path = language._markers_matched_by_code()
    regex_path = language._russian_markers_in_code()
    listed = language._markers_listed_in_skill()

    taken = measurement.Measurement("Cyrillic markers: code against SKILL.md")
    taken.agree("AST walk and line regex", first=sorted(ast_path), second=sorted(regex_path))
    taken.expect(
        measurement.POSITIVE,
        "a marker in the code and in the list is reported listed",
        expected=[],
        got=sorted(ast_path - listed),
    )
    taken.expect(
        measurement.NEGATIVE,
        "a marker in the code but not in the list is reported unlisted",
        expected=["seeded"],
        got=sorted({"seeded"} - listed),
    )
    taken.expect(
        measurement.CONTROL,
        "a string that is not a marker is not counted",
        expected=[],
        got=sorted(marker for marker in ast_path if not language._CYRILLIC.search(marker)),
    )
    taken.count(len(ast_path), plausible=(7, 7))
    assert taken.finish() == 0


# --- the norm carries the rule ----------------------------------------------


def _probe_rule() -> str:
    """The probe rule as `SKILL.md` states it.

    Returns:
        The paragraph from its bold lead to the next blank line.
    """
    text = _SKILL_MD.read_text(encoding="utf-8")
    start = text.find("**Правило для проб.**")
    assert start != -1, "SKILL.md has no probe rule; a rule nobody wrote down is not a rule"
    tail = text[start + len("**Правило для проб.**") :]
    return tail.split("\n\n", 1)[0]


def test_the_skill_states_the_probe_rule_next_to_the_test_rule() -> None:
    """DoD-5: worded like the test rule, and in the same place — §0, where the rule binds."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    assert "**Правило для тестов.**" in text
    assert text.index("**Правило для тестов.**") < text.index("**Правило для проб.**")


def test_the_probe_rule_names_both_controls_and_the_range() -> None:
    """A rule that names only "a control" leaves the reader to invent one."""
    rule = _probe_rule()
    assert "положительн" in rule and "отрицательн" in rule, rule
    assert "диапазон" in rule, rule
    assert "0" in rule and "7" in rule, "the rule must carry the example that failed: 0 where 7 is"
    assert "make_stand" in rule, "the rule must name the stand helper, not leave it to memory"
    assert "Discriminator" not in rule, (
        "a probe has no neighbouring hypothesis; naming Discriminator here would apply a "
        "comparative field to a non-comparative problem"
    )


# --- Discriminator stays where the norm puts it ------------------------------


_H0_WITHOUT_DISCRIMINATOR = (Path(__file__).resolve().parent / "fixtures" / "h0-no-discriminator.md")


def test_a_probe_ledger_validates_without_a_discriminator() -> None:
    """DoD-6: `Discriminator` is a field of a hypothesis among others, and a probe has none.

    The ledger below is a complete phase-2 run whose single hypothesis omits the field. It must
    pass, because the requirement is "at ≥2 hypotheses" and there is one.
    """
    text = _H0_WITHOUT_DISCRIMINATOR.read_text(encoding="utf-8")
    assert "Discriminator" not in text, "the fixture must actually lack the field"
    state = _load("bugfixer_gate_state_st17", _STATE)
    hypothesis = state.ledger_readers.parse_hypotheses(text)["H0"]
    assert hypothesis.discriminator == ""
    assert state.guard_first_guess(text, {}) is None, (
        "a single-hypothesis run was refused for a field the norm requires only at ≥2"
    )
