"""DoD-7 evidence: the end-to-end test fails when any link of the chain is removed.

The chain is only worth testing if each link is load-bearing. This removes one link at a time and
confirms the test goes red — §0's rule applied to the one test whose whole purpose is to show the
path works.

Development helper. Not shipped, not collected by pytest.
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
    """Import a fresh copy of the end-to-end module.

    Returns:
        The imported module, with its original helpers.
    """
    tests_dir = REPO / "skills" / "bug-fixer" / "tests"
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    name = "e2e_probe"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_case(kind: str) -> tuple[bool, str]:
    """Run the whole-run test with one link removed.

    The scratch project must live **outside** the plugin tree: a path containing
    `skills/bug-fixer` is the plugin's own tree, where `--dev-plugin` exemption applies and every
    production write is allowed (invariant 47). A harness inside the tree measures that exemption
    instead of the phase rule, and reports a false defect.

    Args:
        kind: Which link to remove — ``allow_all``, ``no_table`` or ``no_done``.

    Returns:
        ``(caught, detail)`` — whether the test failed, and why.
    """
    module = load()
    with tempfile.TemporaryDirectory() as box:
        if kind == "allow_all":
            # A gate that permits every write: the phase-1 denial must vanish.
            module._write = lambda *a, **k: (0, {"permission": "allow"})
        elif kind == "no_table":
            # Drop the header row's required columns: `results_table_present` looks for
            # `До/После/Порог/Вывод` in a header, so removing the header (not the data rows)
            # is what makes `PROVEN` refuse.
            original = module._append

            def append_without_table(project, text):
                if "| Тест | Что проверяет |" in text:
                    text = "\n".join(
                        line
                        for line in text.splitlines()
                        if not line.startswith("| Тест | Что проверяет |")
                        and not line.startswith("|---|---|---|---|---|---|---|")
                    )
                original(project, text)

            module._append = append_without_table
        else:
            # Swallow the `status` CLI call: without a recorded terminal status the run stays
            # RUNNING, so the closing assertion must notice. The verb is not `args[1]` — the call
            # is `state <ledger> status DONE`, so it is matched by name anywhere in the arguments.
            original_run = module._bugfixer

            def without_status(project, *args):
                if "status" in args:
                    return subprocess.CompletedProcess(list(args), 0, "{}", "")
                return original_run(project, *args)

            module._bugfixer = without_status

        try:
            module.test_the_whole_run_from_doctor_to_done(pathlib.Path(box) / "run")
        except AssertionError as error:
            first = (str(error).splitlines() or ["assertion failed"])[0]
            return True, first[:110]
        return False, "the run still passed with this link removed"


def main() -> int:
    """Remove each link in turn and report whether the test noticed.

    Returns:
        0 when every removal is caught, 1 otherwise.
    """
    cases = {
        "allow_all": "the phase-1 denial (a gate that allows production edits)",
        "no_table": "the PROVEN edge (a verdict without the results table)",
        "no_done": "the closing (a run that never reaches DONE)",
    }
    failures = 0
    for kind, description in cases.items():
        caught, detail = run_case(kind)
        print(f"{'RED ' if caught else 'GREEN'}  {description}")
        print(f"       {detail}")
        if not caught:
            failures += 1
    print(f"\n{'every removed link is caught' if not failures else f'{failures} link(s) not caught'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
