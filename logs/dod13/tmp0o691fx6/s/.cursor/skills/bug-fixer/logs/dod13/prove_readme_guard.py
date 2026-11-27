"""DoD-13 evidence: the README-tree guard fails when a shipped reference is undocumented.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[4]
TEST = REPO / "skills" / "bug-fixer" / "tests" / "test_bugfixer_skill.py"
README = REPO / "skills" / "bug-fixer" / "README.md"


def main() -> int:
    """Remove the matrix line from an in-memory README and confirm the test goes red.

    Returns:
        0 when the guard catches the omission, 1 otherwise.
    """
    tests_dir = REPO / "skills" / "bug-fixer" / "tests"
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    spec = importlib.util.spec_from_file_location("skill_tests", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["skill_tests"] = module
    spec.loader.exec_module(module)

    original = README.read_text(encoding="utf-8")
    stripped = "\n".join(
        line for line in original.splitlines() if "invariant-matrix.json" not in line
    )

    class _Memory:
        """Serves the stripped README in place of the file."""

        def read_text(self, encoding: str = "utf-8") -> str:
            """Return the stripped README.

            Args:
                encoding: Ignored.

            Returns:
                README text without the matrix line.
            """
            return stripped

    module._README = _Memory()  # type: ignore[attr-defined]
    try:
        module.test_the_readme_tree_lists_every_shipped_reference()
    except AssertionError as error:
        print(f"RED   {str(error).splitlines()[0]}")
    else:
        print("GREEN on a README that omits the matrix — the guard does not work")
        return 1

    module._README = README  # type: ignore[attr-defined]
    module.test_the_readme_tree_lists_every_shipped_reference()
    print("GREEN control (unmodified README)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
