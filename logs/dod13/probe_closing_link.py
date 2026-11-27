"""Does the third link mutation actually remove the closing step?

The proof harness reports the closing link as not caught. Either the test does not assert it, or
the monkeypatch never reaches the call. This traces the CLI calls the test makes.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[4]
TEST = REPO / "skills" / "bug-fixer" / "tests" / "test_bugfixer_end_to_end_v3.py"


def load() -> object:
    """Import the e2e module.

    Returns:
        The imported module.
    """
    sys.path.insert(0, str(TEST.parent))
    spec = importlib.util.spec_from_file_location("e2e_trace", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_trace"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Run the test with the status call suppressed, and print what it called.

    Returns:
        0 when the suppression makes the test fail, 1 when the test still passes.
    """
    module = load()
    original = module._bugfixer
    seen: list[tuple[str, ...]] = []

    def spy(project: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Record every CLI call; swallow the `status` one.

        Args:
            project: Scratch project.
            *args: CLI arguments.

        Returns:
            The original result, or an empty success for `status`.
        """
        seen.append(args[:2])
        if len(args) >= 2 and args[1] == "status":
            return subprocess.CompletedProcess(list(args), 0, "{}", "")
        return original(project, *args)

    module._bugfixer = spy
    box = pathlib.Path(tempfile.mkdtemp())
    try:
        module.test_the_whole_run_from_doctor_to_done(box / "run")
    except AssertionError as error:
        print(f"RED: {(str(error).splitlines() or [''])[0][:110]}")
        print(f"cli calls: {seen}")
        return 0
    print(f"PASSED with the status call removed — the assertion is missing. calls: {seen}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
