"""The fallback: a payload with neither `cwd` nor `workspace_roots`.

`_project_root` ends with `return Path.cwd()`. Under a plugin hook the process working directory
is the *plugin* directory, not the project — so this measures exactly the case the user asked
about: could the plugin ever take its own directory for the project root?
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402


def main() -> None:
    print("== the `Path.cwd()` fallback ==")
    print(f"  this process cwd starts at: {Path.cwd()}")

    event: dict = {}
    print(f"  event={{}} (no cwd, no workspace_roots), cwd={Path.cwd().name} -> "
          f"{gate._project_root(event)}")

    os.chdir(SKILL / "hooks")
    print(f"  after chdir to hooks/, event={{}} -> {gate._project_root(event)}")
    try:
        gate.discover_ledger(gate._project_root(event))
        print("  discover_ledger: returned normally")
    except Exception as exc:  # noqa: BLE001
        print(f"  discover_ledger: {type(exc).__name__}")

    empty_cwd = {"cwd": "", "workspace_roots": []}
    print(f"  cwd='' and empty workspace_roots -> {gate._project_root(empty_cwd)}")

    print("\n  Corpus check: does any real Cursor payload omit both signals?")
    import json

    corpus = json.loads((SKILL / "tests/fixtures/cursor_payloads.json").read_text())
    for row in corpus:
        cwd = row.get("cwd")
        roots = row.get("workspace_roots")
        has_cwd = isinstance(cwd, str) and cwd.strip()
        has_roots = isinstance(roots, list) and any(r for r in roots)
        print(f"    {row['tool_name']:<18} cwd={cwd!r:<10} roots={'yes' if has_roots else 'no':<4} "
              f"-> {'payload' if (has_cwd or has_roots) else 'FALLBACK'}")


if __name__ == "__main__":
    main()
