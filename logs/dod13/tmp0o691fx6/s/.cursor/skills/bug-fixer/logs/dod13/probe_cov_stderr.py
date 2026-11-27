"""DoD-13: why `test_the_cli_is_clean_on_a_setup_fact` fails only under `--cov`.

The test asserts the secrets CLI writes nothing to stderr. Coverage is configured with
`patch = ["subprocess"]` (pyproject), so the child interpreter runs under coverage and emits
its own startup warning to stderr. This script captures that stderr both ways, so the cause
is shown rather than guessed — and so the difference from `--cov` is attributable.

Development helper. Not shipped, not collected by pytest.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SECRETS = PLUGIN / "hooks" / "bugfixer_secrets.py"


def main() -> int:
    """Run the secrets CLI with and without coverage and print stderr.

    Returns:
        0 when the difference is reproduced, 1 when both are silent.
    """
    sample = PLUGIN / "logs" / "dod13" / "clean.txt"
    sample.write_text("api_key: configured\nprovider configured=yes\n", encoding="utf-8")

    plain = subprocess.run(
        [sys.executable, str(SECRETS), "--text", str(sample)],
        text=True,
        capture_output=True,
        check=False,
    )
    covered = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "-m", "hooks.bugfixer_secrets", "--text", str(sample)],
        text=True,
        capture_output=True,
        check=False,
        cwd=PLUGIN,
    )

    print(f"plain   : exit={plain.returncode} stderr={plain.stderr!r}")
    print(f"coverage: exit={covered.returncode} stderr={covered.stderr.strip()[:200]!r}")

    if plain.stderr == "" and covered.stderr != "":
        print(
            "\nreproduced: the child writes a coverage warning to stderr. The test asserts "
            "silence, so it cannot pass under `patch = [\"subprocess\"]` — a test/coverage "
            "interaction, not a defect in the scanner."
        )
        return 0

    print("\nnot reproduced: both runs are silent, so something else caused the failure")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
