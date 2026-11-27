"""Why do three OpenClaw mutations survive?

Three tests stayed green under mutation. Either the mutation text did not match, or the test is not
looking where I think it is. This prints the answer for each: whether the anchor exists, and what the
weakened check actually sees.

Development helper. Not shipped.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO / "skills" / "bug-fixer"
ADAPTER = PLUGIN / "hooks" / "openclaw-plugin.ts"
README = PLUGIN / "README.md"


def main() -> int:
    """Report why each mutation is not caught.

    Returns:
        Exit code.
    """
    adapter = ADAPTER.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    print("=== 1. does `blockReason` appear enough times after one replacement? ===")
    mutated = adapter.replace("blockReason", "detail", 1)
    print(f"  blockReason before: {adapter.count('blockReason')}, after: {mutated.count('blockReason')}")
    print(f"  block: true count: {adapter.count('block: true')}")

    print("\n=== 2. what does the empty-reason regex actually match? ===")
    code = re.sub(r"/\*.*?\*/", "", adapter, flags=re.DOTALL)
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("//"))
    for match in re.finditer(r"blockReason:\s*(.+?)(?:,\s*\n|\n\s*\})", code, re.DOTALL):
        print(f"  matched: {match.group(1).strip()[:70]!r}")

    print("\n=== 3. which sentence does the README test see? ===")
    anchor = "**Not executed on a machine.**"
    print(f"  anchor present: {anchor in readme}")
    lowered = readme.lower()
    section = readme[lowered.index("openclaw"):]
    hits = [
        m.group(0)
        for m in re.finditer(r"not (?:been )?(?:run|tested|verified|executed)", section, re.IGNORECASE)
    ]
    print(f"  regex hits in the OpenClaw section: {hits}")
    mutated_readme = readme.replace(anchor, "**Verified.**")
    print(f"  after mutation, hits: "
          f"{[m.group(0) for m in re.finditer(r'not (?:been )?(?:run|tested|verified|executed)', mutated_readme[mutated_readme.lower().index('openclaw'):], re.IGNORECASE)]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
