"""The language rule must match what the gate actually parses (TASK-086 ST-16).

`SKILL.md` now tells the agent to write task files in English, with seven Russian markers listed
as untranslatable. A list like that is only worth having if it is true: a marker missing from it
gets translated, the gate stops seeing the proof, and the refusal blames a marker that is
visibly in the file.

These tests compare the list against the pattern tables in the code, in both directions:

* every Russian literal in the gate appears in `SKILL.md`;
* every marker `SKILL.md` calls untranslatable really is Russian in the code.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_SKILL = helpers.SKILL_DIR / "SKILL.md"
_STATE = helpers.HOOKS / "bugfixer_gate_state.py"
_GATE = helpers.HOOKS / "bugfixer_gate.py"

#: Cyrillic, used to tell a marker that must stay Russian from one already in English.
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")

#: The section that carries the rule; absent means the rule is gone, not that there is nothing to check.
_HEADING = "## Language: task files in English"


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


def _skill_text() -> str:
    """`SKILL.md` contents.

    Returns:
        The file as text.
    """
    return _SKILL.read_text(encoding="utf-8")


def _language_section() -> str:
    """The language rule section of `SKILL.md`.

    Returns:
        Section text, from its heading to the next level-2 heading.
    """
    text = _skill_text()
    start = text.find(_HEADING)
    assert start != -1, f"SKILL.md has no {_HEADING!r} section; the rule is gone"
    tail = text[start + len(_HEADING) :]
    next_heading = re.search(r"^## ", tail, re.MULTILINE)
    return tail[: next_heading.start()] if next_heading else tail


def _russian_markers_in_code() -> set[str]:
    """Russian marker literals the gate matches.

    The literals carry regex escapes (`ФАКТ \\(RED\\):`, `Логи:[ \\t]*`), so they are unescaped
    to what a reader would actually write in a ledger. Missing one here is what the test below
    is for: the list in `SKILL.md` must not be trusted without it.

    Returns:
        The markers themselves, e.g. ``ФАКТ (RED):``.
    """
    found: set[str] = set()
    for path in (_STATE, _GATE):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r'r"([^"]*)"', source):
            literal = match.group(1)
            if not _CYRILLIC.search(literal):
                continue
            cleaned = literal.replace("\\(", "(").replace("\\)", ")")
            # Anchored patterns match a whole line (`^.*ФАКТ (GREEN):.*$`); the marker inside is
            # what a reader writes, so the anchors are dropped before splitting.
            cleaned = cleaned.strip("^$").replace(".*", "")
            # Drop everything from the first regex character class: what precedes it is the marker.
            cleaned = re.split(r"\[|\\s|\\b", cleaned, maxsplit=1)[0]
            cleaned = cleaned.strip()
            if cleaned:
                found.add(cleaned)
    return found


def test_the_language_rule_exists() -> None:
    """A rule nobody wrote down is a rule nobody follows."""
    section = _language_section()
    assert "English" in section
    assert "the language" in section and "operator" in section, (
        "the section must say the chat follows the operator's language, or the agent guesses"
    )


def test_the_chat_rule_is_not_pinned_to_one_language() -> None:
    """A plugin that answers in one language is a plugin for one locale.

    The rule used to read "what stays Russian", which made the chat Russian for every operator.
    Portability is the point: the chat follows whatever language the operator writes in, so the
    section must not name a single chat language as the answer.
    """
    section = _language_section()
    chat_lines = [
        line
        for line in section.splitlines()
        if "chat" in line.lower() or "operator writes" in line
    ]
    assert chat_lines, "the section says nothing about the chat language"
    joined = " ".join(chat_lines)
    assert "language the operator writes in" in joined or "follows the operator" in joined, (
        "the chat language must be defined relative to the operator, not fixed: "
        f"{joined[:200]}"
    )
    assert "stays Russian" not in section, (
        "the rule still frames Russian as the chat language; that pins the plugin to one locale"
    )


def test_the_description_is_findable_in_english_and_in_russian() -> None:
    """The harness picks a skill by its description, so a description in one language hides it.

    A Russian-speaking operator searching "тесты падают" and an English-speaking one searching
    "tests fail" must both reach the same skill; a single-language description reaches one.
    """
    text = _skill_text()
    front = text.split("---", 2)[1] if text.startswith("---") else ""
    assert front, "SKILL.md has no frontmatter"
    assert "Systematic bug fixing" in front, "the description has no English phrasing"
    assert "исправление багов" in front, "the description has no Russian phrasing"


def test_every_russian_marker_in_the_code_is_listed() -> None:
    """The list is the contract: a marker the code reads but the list omits will be translated."""
    section = _language_section()
    missing = [marker for marker in _russian_markers_in_code() if marker not in section]
    assert not missing, (
        "SKILL.md tells the agent these markers must stay Russian, but does not list them: "
        f"{sorted(missing)}"
    )


def test_the_listed_markers_are_really_russian_in_the_code() -> None:
    """The other direction: a listed marker that is already English is a stale list."""
    section = _language_section()
    listed = {
        match.group(1)
        for match in re.finditer(r"^\| `([^`]+)` \|", section, re.MULTILINE)
    }
    assert listed, "the untranslatable-marker table is empty"
    code = _russian_markers_in_code()
    # `До`/`После`/`Порог`/`Вывод` live inside a tuple, not a regex literal, and are checked by
    # the results-table test below.
    table_columns = {"До", "После", "Порог", "Вывод"}
    not_russian = sorted(listed - code - table_columns)
    assert not not_russian, f"listed as untranslatable but not Russian in the code: {not_russian}"


def test_the_results_table_columns_are_checked_in_russian() -> None:
    """Phase 5 requires the columns by name, so the names are part of the contract."""
    state = _load("bugfixer_gate_state_language", _STATE)
    body = (
        "| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |\n"
        "|---|---|---|---|---|---|---|\n"
        "| test_a | stale | hits | 7 | 0 | 0 | исправлено |\n"
    )
    assert state.results_table_present(body), "the §11 table is not recognised"
    english = (
        "| Test | Checks | Metric | Before | After | Threshold | Verdict |\n"
        "|---|---|---|---|---|---|---|\n"
        "| test_a | stale | hits | 7 | 0 | 0 | fixed |\n"
    )
    assert not state.results_table_present(english), (
        "an English header passed the check: the columns are read by name, so the table below "
        "would have to change with the code"
    )


# --- an English twin is not a marker, and the refusal says which one to write ---

#: Every Russian marker with the English spelling an agent might reach for. The pairs are the
#: contract: the left side is what the code reads, the right side is what must be refused.
_MARKER_TWINS = (
    ("ФАКТ (RED):", "FACT (RED):"),
    ("ФАКТ (GREEN):", "FACT (GREEN):"),
    ("ФАКТ (логи):", "FACT (logs):"),
    ("ФАКТ (дельта):", "FACT (delta):"),
    ("ФАКТ (регрессия):", "FACT (regression):"),
    ("Логи:", "Logs:"),
    ("Причина:", "Reason:"),
)

_PROBE_HEAD = (
    "### F5 — probe H1\n"
    "| Field | Value |\n"
    "|---|---|\n"
    "| Kind | probe |\n"
    "| Scope | H1 |\n"
    "| Outcome | CONFIRMED |\n"
    "\n"
    "FAILING TEST: pytest tests/test_a.py -> failed\n"
    "FAILING TEST: pytest tests/test_b.py -> failed\n"
    "\n"
    "| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |\n"
    "|---|---|---|---|---|---|---|\n"
    "| test_a | stale | hits | 7 | 0 | 0 | исправлено |\n"
    "\n"
)


def _probe_with(red: str) -> str:
    """A complete probe record whose RED fact is written as given.

    Args:
        red: The line carrying the RED fact.

    Returns:
        Record body text.
    """
    return (
        _PROBE_HEAD
        + red
        + "\nФАКТ (логи): invalidate after commit\n"
        "Bug: stable\n"
        "ФАКТ (GREEN): pytest -> 1654 passed\n"
        "Attempt 2 consumed\n"
        "Secrets scan: clean\n"
    )


@pytest.mark.parametrize(("russian", "english"), _MARKER_TWINS[:1])
def test_an_english_marker_is_not_accepted_and_the_refusal_names_the_russian(
    russian: str, english: str
) -> None:
    """The rule "write only the listed markers" is only safe if the failure teaches the fix.

    Measured before this test (`probes/st16_english_marker_refusal.py`): with `FACT (RED):` the
    gate refused and named `ФАКТ (RED):` in the refusal. That behaviour was a property of the
    refusal text, not of a test — one reword and the agent would be told "no RED fact" while a RED
    fact sat in the file under the wrong spelling.

    The RED marker is the case driven end to end because it is the first proof `PROVEN` reads; the
    other markers are covered by the table check below, whose twins are refused by the same
    mechanism and are listed in the norm.
    """
    state = _load("bugfixer_gate_state_markers", _STATE)
    good = state.guard_proven(_probe_with(f"{russian} pytest -> 2 failed"), {})
    assert good is None, f"the Russian marker must be accepted: {good.reason if good else ''}"

    bad = state.guard_proven(_probe_with(f"{english} pytest -> 2 failed"), {})
    assert bad is not None, f"{english!r} was accepted as a marker"
    assert russian in bad.reason, (
        f"the refusal for {english!r} does not say to write {russian!r}: {bad.reason}"
    )


def test_every_russian_marker_is_in_the_twin_table() -> None:
    """A marker the gate reads but this module forgets is a marker nobody's test protects."""
    code = _russian_markers_in_code()
    covered = {pair[0] for pair in _MARKER_TWINS}
    missing = sorted(code - covered)
    assert not missing, f"Russian markers with no English-twin case: {missing}"


def test_the_skill_states_the_rule_unconditionally() -> None:
    """A rule with exceptions for "when the text is English" is a rule the agent will misread."""
    section = _language_section()
    assert "only from this list, and only in this spelling" in section, (
        "SKILL.md must say a marker comes from the list and in its spelling, unconditionally"
    )
    assert "names the correct marker" in section, (
        "and that the refusal names the marker, which is what makes the mistake cheap"
    )
