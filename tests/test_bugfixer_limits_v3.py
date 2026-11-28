"""Counters, limits and the secret scanner for bug-fixer 3.0 (TASK-081 ST-3/ST-4/ST-4a).

Three rules live here, and each exists because its absence produced a specific
defect in 2.1:

* **No hashing of the ledger.** Debugging is research: the agent mis-writes a record, refines a
  formulation, re-reads an early conclusion. A mechanism that blocks fixing your
  own typo protects nothing and gets disabled. What remains is a *warning* when
  the ``## Task`` block changes, because fixing a different bug is a different
  kind of problem from fixing your own sentence.
* **Numbering is visible.** ``Attempt`` runs ``{1..n}`` without gaps: a deleted
  line is then obvious, and an attempt count nobody can trust makes every cap
  decorative. ``Recheck`` is per hypothesis and does not touch ``A`` — repairing
  the test is repairing the instrument.
* **Secrets have one level.** A value is a leak, and so is anything derived from it — length,
  hash, size, prefix — because those narrow or verify a brute-force search. The *fact* of setup
  (`api_key: configured`) is not a leak: it is the only thing §10 lets an agent report, and the
  operator needs it. The old advisory level was removed on 2026-11-27 (see
  `test_bugfixer_secrets_derived_v3.py`).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_HOOKS = jrnl.HOOKS
_GATE = _HOOKS / "bugfixer_gate.py"
_SECRETS = _HOOKS / "bugfixer_secrets.py"


def _load(name: str, path: Path):
    """Import a module from a file path, registering it for dataclass lookup."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def gate():
    """The gate module."""
    return _load("bugfixer_gate_limits", _GATE)


@pytest.fixture(scope="module")
def secrets_module():
    """The secret scanner."""
    return _load("bugfixer_secrets_test", _SECRETS)


# --- ST-3: no hashing --------------------------------------------------------


def test_rewriting_an_old_record_is_allowed(gate, tmp_path: Path) -> None:
    """Debugging is research: fixing your own early note must not be blocked."""
    ledger = tmp_path / "T.md"
    ledger.write_text(
        "## Task\nfix it\n\n### F1 — baseline\n| Kind | research |\nOLD TEXT\n", encoding="utf-8"
    )
    ledger.write_text(
        "## Task\nfix it\n\n### F1 — baseline\n| Kind | research |\nNEW TEXT\n", encoding="utf-8"
    )
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "src/a.py"})
    assert "record" not in verdict.reason.lower()


def test_no_integrity_module_exists() -> None:
    """Invariant: the hash module is not created, so nothing can quietly depend on it."""
    assert not (_HOOKS / "bugfixer_ledger_integrity.py").exists()


def test_task_fingerprint_is_recorded_on_first_sight(gate, tmp_path: Path) -> None:
    """The baseline is the task text the run started with, kept as a journal record."""
    ledger = tmp_path / "T.md"
    ledger.write_text("## Task\nfix the cache\n", encoding="utf-8")
    assert gate.task_drift(ledger, ledger.read_text(encoding="utf-8")) == ""
    starts = [r for r in gate.journal.read_events(ledger) if r["event"] == "start"]
    assert starts and starts[0]["task_fingerprint"]


def test_task_drift_warns_but_does_not_refuse(gate, tmp_path: Path) -> None:
    """Fixing a different bug is worth a warning; the operator decides."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text("## Task\nfix the cache\n", encoding="utf-8")
    gate.task_drift(ledger, ledger.read_text(encoding="utf-8"))

    ledger.write_text("## Task\nfix something else entirely\n", encoding="utf-8")
    verdict = gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"})
    assert verdict.allow
    assert gate.task_drift(ledger, ledger.read_text(encoding="utf-8")) != ""


def test_clarifications_do_not_count_as_task_drift(gate, tmp_path: Path) -> None:
    """Refinements belong in ``## Clarifications``, and adding them is legal."""
    ledger = tmp_path / "T.md"
    ledger.write_text("## Task\nfix the cache\n", encoding="utf-8")
    before = gate.task_fingerprint(ledger.read_text(encoding="utf-8"))
    ledger.write_text(
        "## Task\nfix the cache\n\n## Clarifications\nQ: a  A: b\n", encoding="utf-8"
    )
    after = gate.task_fingerprint(ledger.read_text(encoding="utf-8"))
    assert before == after


# --- ST-4: numbering and limits ----------------------------------------------


def test_attempt_gap_is_named(gate) -> None:
    """A deleted attempt line must be visible, not silently forgiven."""
    problems = gate.counter_problems("Attempt 1 consumed\nAttempt 3 consumed\n")
    assert any("gap at 2" in problem for problem in problems)


def test_attempts_without_a_gap_pass(gate) -> None:
    """The legal numbering must pass, or the rule is unusable."""
    assert gate.counter_problems("Attempt 1 consumed\nAttempt 2 consumed\n") == []


def test_twelve_attempts_exceed_the_cap(gate) -> None:
    """The cap is on ``A``, and eleven attempts is already over it."""
    text = "".join(f"Attempt {n} consumed\n" for n in range(1, 13))
    assert any("cap is 10" in problem for problem in gate.counter_problems(text))


def test_recheck_gap_is_named_with_its_hypothesis(gate) -> None:
    """``R<h>`` is per hypothesis; a gap there is a different bug from a gap in ``A``."""
    problems = gate.counter_problems("Recheck H1 #1\nRecheck H1 #3\n")
    assert any("Recheck H1" in problem and "gap at #2" in problem for problem in problems)


def test_sixth_recheck_is_refused(gate) -> None:
    """Five repairs is the limit; a sixth attempt is not evidence."""
    text = "".join(f"Recheck H1 #{n}\n" for n in range(1, 7))
    problems = gate.counter_problems(text)
    assert any("limit of 5" in problem for problem in problems)


def test_recheck_without_a_probe_is_refused(gate) -> None:
    """There is no verdict to repeat when the hypothesis was never probed."""
    problems = gate.counter_problems("Recheck H1 #1\n")
    assert any("without a probe" in problem for problem in problems)


def test_rechecks_do_not_consume_attempts(gate) -> None:
    """Invariant 6: ten hypotheses times five repairs is still ``A`` = 10."""
    text = ""
    for hypothesis in range(1, 11):
        text += f"### H{hypothesis} — claim\n| Rank | {hypothesis} |\n"
        text += f"\n### F{hypothesis} — probe\n| Kind | probe |\n| Scope | H{hypothesis} |\n"
        text += f"Attempt {hypothesis} consumed\n"
        for recheck in range(1, 6):
            text += f"Recheck H{hypothesis} #{recheck}\n"
    assert gate.counter_problems(text) == []
    assert gate.count_attempts(text) == 10


def test_eleven_hypotheses_are_refused(gate) -> None:
    """Thirty hypotheses with none probed is not research."""
    text = "".join(f"### H{n} — claim\n| Rank | {n} |\n" for n in range(12))
    problems = gate.counter_problems(text)
    assert any("limit is 10" in problem for problem in problems)


def test_ten_hypotheses_are_allowed(gate) -> None:
    """The limit must not forbid the number it names."""
    text = "".join(f"### H{n} — claim\n| Rank | {n} |\n" for n in range(10))
    assert gate.counter_problems(text) == []


def test_unauthorized_reset_is_reported_as_a_problem(gate) -> None:
    """A reset the operator did not write makes the cap decorative."""
    problems = gate.counter_problems("Attempt 1 consumed\nCounter reset: A -> 0\n")
    assert any("operator" in problem for problem in problems)


def test_operator_reset_restarts_the_numbering(gate) -> None:
    """After a reset the numbering starts at 1 again, and that is not a gap."""
    text = (
        "Attempt 1 consumed\nAttempt 2 consumed\n"
        "Counter reset: A -> 0 (by operator: new baseline)\n"
        "Attempt 1 consumed\n"
    )
    assert gate.counter_problems(text) == []


# --- ST-4a: secret scanner ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("password=hunter2", "assignment"),
        ("api_key: abcdef123456", "assignment"),
        ("Authorization: Bearer abcdefghijklmnop", "bearer"),
        ("token sk-abcdefghijklmnopqrst", "openai"),
        ("ghp_abcdefghijklmnopqrstuvwx", "github"),
        ("AKIAIOSFODNN7EXAMPLE", "aws"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk", "jwt"),
    ],
)
def test_each_refusal_pattern_fires(secrets_module, text: str, rule: str) -> None:
    """Every refusal pattern needs its own proof: one test for the group proves one regex."""
    findings = secrets_module.scan(text)
    assert findings, f"no finding for {text!r}"
    assert findings[0].rule == rule


def test_a_fact_of_setup_is_allowed(secrets_module) -> None:
    """`password_present=true` reports setup, which §10 requires an agent to be able to say.

    Before 2026-11-27 this was a *warning*: the operator was told "suspicious" about the one
    statement the spec needs, while `api_key: configured` — the same fact in other words — was
    refused outright. The advisory level is gone; see `test_bugfixer_secrets_derived_v3.py`.
    """
    assert secrets_module.scan("password_present=true") == []


def test_harmless_flag_is_not_even_a_warning(secrets_module) -> None:
    """A false positive here gets the gate removed, taking the real checks with it."""
    findings = secrets_module.scan("cache_present=true")
    assert findings == []


def test_the_refusal_does_not_repeat_the_secret(secrets_module) -> None:
    """A message that quotes the value is a second copy of it, in an issue tracker."""
    findings = secrets_module.scan("api_key=supersecretvalue123")
    assert findings
    assert "supersecretvalue123" not in secrets_module.describe(findings[0])
    assert "***" in secrets_module.describe(findings[0])


def test_an_env_var_reference_is_not_a_secret(secrets_module) -> None:
    """``password: ${DB_PASSWORD}`` is configuration, not a leak."""
    assert secrets_module.scan('password: "${DB_PASSWORD}"') == []


def test_a_null_value_is_not_a_secret(secrets_module) -> None:
    """``token: null`` says there is no token."""
    assert secrets_module.scan("token: null") == []


def test_writing_a_record_with_a_secret_is_denied(gate, tmp_path: Path) -> None:
    """Records hold log output, so they are where a leaked value lands.

    What is refused is the **write** of a secret (operator's ruling 2026-11-28 15:02). The
    previous version of this test asserted the refusal while writing to `tests/a.py` — a file that
    holds no secret — which made the rule about access rather than about writing.
    """
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text("## Task\nfix\n\n### F1 — research\n| Kind | research |\n", encoding="utf-8")
    poisoned = (
        "## Task\nfix\n\n### F1 — research\n"
        "| Kind | research |\n"
        "[DEBUG] api_key=abcdef123456\n"
    )
    verdict = gate.decide(
        ledger=ledger, tool="write", args={"file_path": str(ledger), "content": poisoned}
    )
    assert not verdict.allow
    # The refusal names the file and the line; the record id is no longer the only marker, because
    # the scan now judges the body being written rather than the parsed `### F<n>` blocks.
    assert "T.md" in verdict.reason
    assert "abcdef123456" not in verdict.reason


def test_a_lingering_secret_does_not_block_unrelated_work(gate, tmp_path: Path) -> None:
    """Access is not writing: a secret already on disk must not stop ordinary edits.

    Measured before the change (`probes/secret_presence_vs_write.py`): a single stale record
    refused writes to the ledger, logs and documentation — work that writes no secret at all.
    """
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text(
        "## Task\nfix\n\n### F1 — research\n"
        "| Kind | research |\n"
        "[DEBUG] api_key=abcdef123456\n",
        encoding="utf-8",
    )
    assert gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"}).allow


def test_clean_record_is_allowed(gate, tmp_path: Path) -> None:
    """The scanner must not block ordinary log lines that report setup."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text(
        "## Task\nfix\n\n### F1 — research\n"
        "| Kind | research |\n"
        "[DEBUG] api_key: configured provider configured=yes\n",
        encoding="utf-8",
    )
    assert gate.decide(ledger=ledger, tool="write", args={"file_path": "tests/a.py"}).allow


def test_writing_a_key_length_is_denied(gate, tmp_path: Path) -> None:
    """The same record with a length instead of the fact: a length helps to recover the value.

    Judged on the write, per the ruling that separates writing from access.
    """
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text("## Task\nfix\n\n### F1 — research\n| Kind | research |\n", encoding="utf-8")
    poisoned = (
        "## Task\nfix\n\n### F1 — research\n"
        "| Kind | research |\n"
        "[DEBUG] api_key_len=51 provider configured=yes\n"
    )
    verdict = gate.decide(
        ledger=ledger, tool="write", args={"file_path": str(ledger), "content": poisoned}
    )
    assert not verdict.allow
    assert "derived" in verdict.reason


def test_cli_reports_a_secret_in_a_file(tmp_path: Path) -> None:
    """The agent needs the same check without importing the module."""
    path = tmp_path / "dirty.txt"
    path.write_text("password=hunter2\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SECRETS), "--text", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "hunter2" not in result.stderr


def test_cli_returns_zero_on_a_clean_file(tmp_path: Path) -> None:
    """Exit 0 is what the agent's own pre-commit pass keys on."""
    path = tmp_path / "clean.txt"
    path.write_text("api_key: configured\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SECRETS), "--text", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
