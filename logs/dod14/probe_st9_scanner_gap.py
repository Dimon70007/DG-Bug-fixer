"""ST-9 probe 4: how well does the existing scanner mask a shell command line?

Probe 3 found `export OPENAI_API_KEY=plainvalue123` reported clean. This probe names the
exact reason and measures how far the hole reaches, because the `target` field would be the
first place a command line reaches the journal — the gate never scans the journal today.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_secrets as secrets  # noqa: E402

NAMES = [
    "API_KEY=a1b2c3d4",
    "OPENAI_API_KEY=a1b2c3d4",
    "OPENAI_API_KEY: a1b2c3d4",
    "api_key=a1b2c3d4",
    "API_KEY: a1b2c3d4",
    "MY_SECRET_TOKEN=a1b2c3d4",
    "SECRET=a1b2c3d4",
    "PASSWORD=a1b2c3d4",
    "DB_PASSWORD=a1b2c3d4",
    "DB_PASSWORD=configured",
]


def main() -> None:
    print("== assignment rule: name shape vs verdict ==")
    for raw in NAMES:
        findings = secrets.scan(raw)
        verdict = "CLEAN" if not findings else findings[0].rule
        print(f"  {raw:34} -> {verdict}")

    print("\n== why: the word boundary in front of `api_key` ==")
    pattern = dict(secrets.SECRET_PATTERNS)["assignment"]
    for raw in ("api_key=x", "OPENAI_API_KEY=x", "DB_PASSWORD=x"):
        print(f"  {raw:20} match={bool(re.search(pattern, raw))}")

    print("\n== the pattern text ==")
    print("  " + pattern)


if __name__ == "__main__":
    main()
