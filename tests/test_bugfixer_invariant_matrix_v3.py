"""The invariant matrix is complete in both directions (DoD-13).

A matrix that only lists what is covered is a claim; a matrix that also fails when a new test
appears unnamed is a gate. Both directions are checked here:

* every invariant of §13 names its tests, or says in one line why no test can hold it;
* every test in the suite is named by an invariant or by the task requirement it belongs to —
  so a new test that protects nothing is visible the moment it is added.

The matrix itself lives in ``references/invariant-matrix.json`` because the plugin must be
testable in a clone that has never seen the spec. The spec's wording inside it is guarded
against drift by ``tests/test_bugfixer_spec_fixture.py`` in the parent repository.
"""

from __future__ import annotations

import ast
import json

import bugfixer_helpers as helpers

MATRIX = helpers.SKILL_DIR / "references" / "invariant-matrix.json"
TESTS = helpers.SKILL_DIR / "tests"


def _matrix() -> dict[str, object]:
    """The matrix as a mapping.

    Returns:
        Parsed matrix.

    Raises:
        AssertionError: When the matrix is missing — absence would make every check vacuous.
    """
    assert MATRIX.is_file(), f"the matrix is missing: {MATRIX}"
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def _suite() -> dict[str, str]:
    """Every test in the plugin's suite.

    Returns:
        ``stem::name`` mapped to the module file name.
    """
    found: dict[str, str] = {}
    for path in sorted(TESTS.glob("test_bugfixer_*.py")):
        stem = path.name[len("test_bugfixer_") : -len(".py")]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                found[f"{stem}::{node.name}"] = path.name
    return found


def test_the_matrix_holds_all_fifty_four_invariants() -> None:
    """§13 has 54 entries: 1–49 plus 17a, 17b and 27a–27c.

    The count is asserted rather than derived so that a spec edit which adds, drops or
    renumbers an invariant forces this file to be revisited.
    """
    invariants = _matrix()["invariants"]
    assert isinstance(invariants, dict)
    expected = {str(n) for n in range(1, 50)} | {"17a", "17b", "27a", "27b", "27c"}
    assert set(invariants) == expected, (
        f"the matrix and §13 disagree: missing {sorted(expected - set(invariants))}, "
        f"extra {sorted(set(invariants) - expected)}"
    )


def test_every_invariant_names_a_test_or_says_why_it_cannot() -> None:
    """An invariant with neither is the gap this matrix exists to make visible."""
    bare = [
        key
        for key, entry in _matrix()["invariants"].items()
        if not entry.get("tests") and not entry.get("unautomated")
    ]
    assert not bare, f"invariants with no test and no reason: {bare}"


def test_an_unautomated_invariant_carries_a_reason() -> None:
    """'Not automated' is an argument with a section behind it, never a shrug."""
    vague = [
        key
        for key, entry in _matrix()["invariants"].items()
        if entry.get("unautomated") and len(entry["unautomated"]) < 40
    ]
    assert not vague, f"too terse to be a reason: {vague}"


def test_every_named_test_exists() -> None:
    """A matrix pointing at a renamed test would keep claiming coverage after it vanished."""
    suite = _suite()
    missing = [
        (key, node)
        for key, entry in _matrix()["invariants"].items()
        for node in entry.get("tests", [])
        if node not in suite
    ]
    for requirement, entry in _matrix()["support"].items():
        missing += [(requirement, node) for node in entry["tests"] if node not in suite]
    assert not missing, f"the matrix names tests that do not exist: {missing}"


def test_every_test_in_the_suite_is_named() -> None:
    """The other direction: an unnamed test is work nobody has said the purpose of."""
    matrix = _matrix()
    named: set[str] = set()
    for entry in matrix["invariants"].values():
        named.update(entry.get("tests", []))
    for entry in matrix["support"].values():
        named.update(entry["tests"])
    unnamed = sorted(set(_suite()) - named)
    assert not unnamed, f"tests named by no invariant and no requirement: {unnamed}"


def test_no_test_is_named_twice() -> None:
    """Two owners for one test means neither owner knows what breaks when it fails."""
    matrix = _matrix()
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for key, entry in matrix["invariants"].items():
        for node in entry.get("tests", []):
            if node in seen:
                clashes.append(f"{node}: invariant {seen[node]} and {key}")
            seen[node] = key
    for requirement, entry in matrix["support"].items():
        for node in entry["tests"]:
            if node in seen:
                clashes.append(f"{node}: {seen[node]} and {requirement}")
            seen[node] = requirement
    assert not clashes, f"tests with two owners: {clashes}"


def test_the_matrix_says_what_it_is_for() -> None:
    """A generated file with no explanation is one nobody regenerates correctly."""
    about = _matrix()["about"]
    assert "§13" in about and "DoD-13" in about, f"the matrix does not explain itself: {about}"
