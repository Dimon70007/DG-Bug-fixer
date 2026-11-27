"""Measure coverage of the lines this change touched, for the `tested:` claim (§11a).

The claim in `CHANGELOG.md` is about one change, not about the project: a 5-line edit can sit
uncovered at 95% project coverage. So this reads `coverage json` and reports only the lines that
differ from git HEAD.

Output goes to stdout as `changed: <covered>/<total> = <n>%`, which is what a person copies into
the changelog. Nothing is written outside the plugin directory (invariant 43).

    python3 scripts/changed_coverage.py --base HEAD --paths hooks lib

Exit codes: 0 when measured, 2 when the measurement cannot be made — the plugin refuses to invent
a number (§0).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

#: Coverage's report, written inside the plugin directory: never `/tmp` (invariant 43).
REPORT_FILE: Final[str] = "logs/coverage.json"

#: Extensions `coverage.py` can measure at all. A file outside this set is not *untested* — it is
#: invisible to the tool, and counting its changed lines in the denominator reports a change as
#: uncovered for having a shell wrapper in it. The distinction matters in both directions:
#: `bin/bugfixer` has no suffix and no record until `source` names it, and that absence is a real
#: miss; `hooks/run-gate.sh` can never have a record, and that absence is not a failure.
MEASURABLE_SUFFIXES: Final[tuple[str, ...]] = (".py", ".pyi")


def _changed_lines(base: str, paths: list[str]) -> dict[str, set[int]]:
    """Lines added or modified relative to a git revision.

    Args:
        base: Revision to diff against.
        paths: Directories to restrict the diff to.

    Returns:
        File path -> added line numbers.
    """
    result = subprocess.run(
        ["git", "diff", "--unified=0", base, "--", *paths],
        text=True,
        capture_output=True,
        check=False,
    )
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            changed.setdefault(current, set())
            continue
        if not line.startswith("@@") or current is None:
            continue
        fragment = line.split("+", 1)[1].split(" ", 1)[0]
        start, _, count = fragment.partition(",")
        first = int(start)
        width = int(count) if count else 1
        changed[current].update(range(first, first + width))
    return {path: lines for path, lines in changed.items() if lines}


def main(argv: list[str] | None = None) -> int:
    """Report changed-line coverage.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` when the number was measured, ``2`` when it could not be.
    """
    parser = argparse.ArgumentParser(prog="changed_coverage.py")
    parser.add_argument("--base", default="HEAD", help="git revision to diff against")
    parser.add_argument(
        "--paths", nargs="+", default=["hooks", "lib"], help="directories to measure"
    )
    parser.add_argument(
        "--coverage-json",
        default=REPORT_FILE,
        help="coverage.py JSON report, produced by `coverage json -o <file>`",
    )
    args = parser.parse_args(argv)

    report = Path(args.coverage_json)
    if not report.is_file():
        print(
            f"no coverage report at {report}. Produce one first:\n"
            "  python -m pytest --cov=hooks --cov=lib && coverage json -o logs/coverage.json",
            file=sys.stderr,
        )
        return 2

    payload = json.loads(report.read_text(encoding="utf-8"))
    executed: dict[str, set[int]] = {}
    executable: dict[str, set[int]] = {}
    for name, info in payload.get("files", {}).items():
        hit = set(info.get("executed_lines", []))
        executed[name] = hit
        # Coverage's own accounting: a line is executable if it was run or reported missing.
        # Comments, docstrings and blank lines are in neither set, and counting them in the
        # denominator would report a change as uncovered because it is documented.
        executable[name] = hit | set(info.get("missing_lines", []))

    changed = _changed_lines(args.base, args.paths)
    if not changed:
        print("no changed lines in the measured paths", file=sys.stderr)
        return 2

    total = covered = 0
    skipped: list[str] = []
    for name, lines in changed.items():
        hit = executed.get(name)
        live = executable.get(name)
        if (hit is None or live is None) and not name.endswith(MEASURABLE_SUFFIXES):
            # Not a miss: `coverage.py` never reports a shell script, so the file has no record by
            # construction. Named rather than dropped, because a silently shrinking denominator is
            # how a number stops meaning what it says.
            skipped.append(f"  {name}: cannot measure (not a Python file; {len(lines)} changed lines)")
            continue
        if hit is None or live is None:
            # A changed Python file with no coverage record is untested, not skipped: counting it
            # as covered would inflate the number in the one place that decides a release.
            total += len(lines)
            print(f"  {name}: no coverage data ({len(lines)} changed lines)", file=sys.stderr)
            continue
        measurable = lines & live
        total += len(measurable)
        covered += len(measurable & hit)

    for note in skipped:
        print(note, file=sys.stderr)

    if total == 0:
        print("changed lines could not be matched to coverage data", file=sys.stderr)
        return 2
    print(f"changed: {covered}/{total} = {round(100 * covered / total)}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
