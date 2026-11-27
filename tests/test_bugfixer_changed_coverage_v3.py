"""What `changed_coverage.py` counts (§11a, invariant 49).

The number decides whether a `CHANGELOG.md` entry may be written, so the denominator matters as
much as the numerator. Two errors are possible and both were seen:

* a file with **no** coverage record is untested — that is why the `bin/bugfixer` gap mattered:
  the entry point had no record, so it silently read as zero;
* a file coverage **cannot** measure is not untested — a shell wrapper is never importable by
  `coverage.py`, and counting it as missed reports a change as uncovered for having a wrapper.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl

_SCRIPT = jrnl.SKILL_DIR / "scripts" / "changed_coverage.py"


def _report(tmp_path: Path, entries: dict[str, dict[str, list[int]]]) -> Path:
    """Write a coverage.py JSON report with these files."""
    path = tmp_path / "coverage.json"
    payload = {
        "files": {
            name: {
                "executed_lines": info.get("executed", []),
                "missing_lines": info.get("missing", []),
            }
            for name, info in entries.items()
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(report: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the measurer against a project-shaped git repo."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--coverage-json", str(report), *args],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def _git_repo(tmp_path: Path) -> Path:
    """A repository with one committed file, so a diff against HEAD exists."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "pkg" / "mod.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "pkg" / "run.sh").write_text("echo hi\necho bye\n", encoding="utf-8")
    return tmp_path


def test_a_python_file_without_a_coverage_record_counts_as_missed(tmp_path: Path) -> None:
    """The `bin/bugfixer` lesson: absent from the report is untested, not exempt.

    Reporting it as zero is the error that once made the whole number describe everything except
    the change under test.
    """
    repo = _git_repo(tmp_path)
    report = _report(tmp_path, {})
    result = _run(report, repo, "--paths", "pkg")
    assert result.returncode == 0, result.stderr
    assert "no coverage data" in result.stderr
    # One miss, not two: `run.sh` cannot be measured at all and is named separately.
    assert "0/1 = 0%" in result.stdout
    assert "cannot measure" in result.stderr


def test_a_file_coverage_cannot_measure_is_left_out_of_the_denominator(tmp_path: Path) -> None:
    """A shell wrapper is not untested; it is unmeasurable by `coverage.py`.

    Counting it as missed reports the change as uncovered because it contains a wrapper — the
    opposite error to the one above, and it hides the real number just as effectively.
    """
    repo = _git_repo(tmp_path)
    report = _report(
        tmp_path, {"pkg/mod.py": {"executed": [1, 2], "missing": []}}
    )
    result = _run(report, repo, "--paths", "pkg")
    assert result.returncode == 0, result.stderr
    assert "1/1 = 100%" in result.stdout, result.stdout
    assert "run.sh" in result.stderr, "the skipped file is not named, so the reader cannot tell"
    assert "cannot measure" in result.stderr.lower() or "not measurable" in result.stderr.lower()


def test_a_missing_python_file_is_not_excused_by_its_suffix(tmp_path: Path) -> None:
    """The rule is "coverage cannot measure it", not ".sh is fine" — a name is not a guarantee."""
    repo = _git_repo(tmp_path)
    (repo / "pkg" / "typed.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add"], cwd=repo, capture_output=True, check=True)
    (repo / "pkg" / "typed.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    report = _report(tmp_path, {"pkg/mod.py": {"executed": [1, 2], "missing": []}})
    result = _run(report, repo, "--paths", "pkg")
    assert "no coverage data" in result.stderr
    assert "typed.py" in result.stderr
