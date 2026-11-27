"""DoD-13 evidence: the scoped invariant-17 tests fail on real defects and stay quiet otherwise.

Two things to prove after the correction:

1. every check still goes red on a genuine defect (a marker losing its writer, a writer that does
   not exist, the pinned `NEW INFORMATION:` disagreement changing, `Outcome` losing a cycle end);
2. the checks no longer report `Outcome` or `STATUS: BLOCKED` as rule-1 defects — those were my
   reader-scope error, not the spec's fault.

The spec is mutated in memory; the file is never written.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[4]
TEST = REPO / "tests" / "test_bugfixer_spec_invariant_writers.py"
SPEC = REPO / "docs" / "bug-fixer-v3-spec.md"


def load() -> object:
    """Import the test module by path.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location("inv17", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["inv17"] = module
    spec.loader.exec_module(module)
    return module


class _Memory:
    """Serves mutated spec text in place of the file."""

    def __init__(self, text: str) -> None:
        """Store the text.

        Args:
            text: Replacement spec body.
        """
        self._text = text

    def is_file(self) -> bool:
        """The path would exist; only the content differs.

        Returns:
            True.
        """
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        """Return the mutated text.

        Args:
            encoding: Ignored; kept for signature parity.

        Returns:
            The mutated spec.
        """
        return self._text


def run(module: object, name: str) -> tuple[bool, str]:
    """Call one test.

    Args:
        module: The imported module.
        name: Test function name.

    Returns:
        ``(failed, detail)``.
    """
    try:
        getattr(module, name)()
    except AssertionError as error:
        # A bare `assert x` carries no message; the failure itself is the signal.
        first = (str(error).splitlines() or ["assertion failed"])[0]
        return True, first[:100]
    return False, "passed"


def main() -> int:
    """Run each mutation and confirm the outcome.

    Returns:
        0 when every case behaves as expected, 1 otherwise.
    """
    module = load()
    original = SPEC.read_text(encoding="utf-8")

    cases: list[tuple[str, str, str, str, bool]] = [
        (
            "a marker loses its writer step",
            "test_every_marker_names_a_writer_step",
            "FAILING TEST: <команда>         1.2",
            "FAILING TEST: <команда>         —  ",
            True,
        ),
        (
            "a writer step does not exist in §6",
            "test_every_writer_step_exists_in_the_section_six_tables",
            "BASELINE: <pass>/<total>…       1.4",
            "BASELINE: <pass>/<total>…       9.9",
            True,
        ),
        (
            "§6 loses the step a marker points at",
            "test_every_writer_step_exists_in_the_section_six_tables",
            "| 4.6a | Просканировать дифф",
            "| 4.6z | Просканировать дифф",
            True,
        ),
        (
            "step 4.8 stops naming NEW INFORMATION (the corrected defect returns)",
            "test_every_edge_read_marker_is_named_by_its_writer",
            "`Revert: <файлы>` + `NEW INFORMATION: <что нового>` при возврате в 3",
            "`Revert: <файлы>`",
            True,
        ),
        (
            "QUEUE_EXHAUSTED stops requiring the marker",
            "test_the_two_four_to_three_edges_both_require_new_information",
            'AND  has "NEW INFORMATION:"\n                                                  → искать новые гипотезы',
            "→ искать новые гипотезы",
            True,
        ),
        (
            "someone renames the marker",
            "test_the_marker_survives_under_its_original_name",
            "NEW INFORMATION: <что нового>   4.8",
            "WHY BACK: <почему>   4.8",
            True,
        ),
        (
            "Outcome loses its research writer",
            "test_outcome_is_written_at_every_cycle_end",
            "| 3.7 | Зафиксировать итог и назвать активную гипотезу (следующая по `Rank` среди `PROPOSED`) | `Outcome \\| RESEARCH: …` + `Working on: H<n>` |",
            "| 3.7 | Зафиксировать итог и назвать активную гипотезу (следующая по `Rank` среди `PROPOSED`) | `Working on: H<n>` |",
            True,
        ),
    ]

    failures = 0
    for description, test_name, old, new, expect_red in cases:
        if old not in original:
            print(f"SKIP  {description}: anchor not found")
            failures += 1
            continue
        module._SPEC = _Memory(original.replace(old, new, 1))  # type: ignore[attr-defined]
        failed, detail = run(module, test_name)
        verdict = "OK  " if failed == expect_red else "BAD "
        want = "red" if expect_red else "green"
        print(f"{verdict}{description}: expected {want}, got {'RED' if failed else 'green'}")
        print(f"       {test_name} -> {detail}")
        if failed != expect_red:
            failures += 1

    # The correction itself: rule 1's scope must not flag Outcome or STATUS: BLOCKED.
    module._SPEC = _Memory(original)  # type: ignore[attr-defined]
    edge_ok, _ = run(module, "test_every_edge_read_marker_is_named_by_its_writer")
    outcome_ok, _ = run(module, "test_outcome_is_written_at_every_cycle_end")
    print(f"\ncontrol: edge-scope test {'RED' if edge_ok else 'green'}, outcome test {'RED' if outcome_ok else 'green'}")
    if edge_ok or outcome_ok:
        failures += 1

    print(f"\n{'every case behaves as expected' if not failures else f'{failures} case(s) wrong'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
