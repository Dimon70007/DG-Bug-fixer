"""Tests for the autonomous bug-fix harness (TASK-080).

The harness exists to answer one question the agent cannot answer honestly: is the
bug fixed? These tests pin the answer to the test runner, never to the agent's
prose, and pin every stop condition so a run cannot spin forever.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_RUNNER = jrnl.SKILL_DIR / "runner" / "bugfixer_run.py"


def _script(path: Path, body: str) -> Path:
    """Write an executable shell script."""
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the runner."""
    return subprocess.run(
        [sys.executable, str(_RUNNER), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _runner_module():
    """Import the runner for its prompt builder.

    The module is registered in ``sys.modules`` before execution: it declares
    dataclasses, and ``dataclasses`` resolves string annotations through that
    registry — without the registration the import fails on the first dataclass.
    """
    spec = importlib.util.spec_from_file_location("bugfixer_run_prompt_test", _RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    """A ledger with a reproduction and a baseline, so Phase 1 is satisfied."""
    path = tmp_path / "TASK-999.md"
    path.write_text(
        "# Bug\n\n## Iteration 1\n\n"
        "FAILING TEST: pytest tests/x.py -> 1 failed\n"
        "BASELINE: 10/10 green\n",
        encoding="utf-8",
    )
    return path


# --- the central promise: the agent cannot declare success -------------------


def test_agent_claiming_success_with_a_red_suite_is_blocked(tmp_path: Path, ledger: Path) -> None:
    """The whole point: an agent saying "fixed" while the suite fails ends BLOCKED."""
    liar = _script(tmp_path / "liar.sh", '#!/bin/bash\necho "All tests pass! STATUS: DONE."\nexit 0\n')

    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 1",
        "--agent", "command",
        "--agent-cmd", f"{liar} {{prompt}}",
        "--max-passes", "3",
        "--run-dir", str(tmp_path / "run"),
    )
    assert result.returncode == 1, result.stdout
    assert "STATUS: BLOCKED" in ledger.read_text(encoding="utf-8")
    assert "STATUS: DONE" not in ledger.read_text(encoding="utf-8").split("STATUS: BLOCKED")[0]


def test_green_suite_ends_done_when_a_results_table_exists(tmp_path: Path) -> None:
    """A green suite plus a results table is the one signal that may end a run DONE."""
    ledger = tmp_path / "TASK-999.md"
    ledger.write_text(
        "# Bug\n\n## Iteration 1\n\n"
        "FAILING TEST: pytest tests/x.py -> 1 failed\n"
        "BASELINE: 10/10 green\n\n"
        "### H1 — claim\n| Rank | 1 |\n| Proposed | F1 |\n| Origin | description |\n"
        "| Mechanism | m |\n| Prediction | p |\n\n#### Records\n- F1 → PROPOSED\n\n"
        "### F2 — Проба H1\n| Field | Value |\n|---|---|\n| Kind | probe |\n| Scope | H1 |\n"
        "| Outcome | CONFIRMED |\n\n"
        "| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |\n"
        "|---|---|---|---|---|---|---|\n"
        "| `test_x` | behaviour | `hits` | 7 | 0 | 0 | исправлено |\n",
        encoding="utf-8",
    )
    agent = _script(tmp_path / "ok.sh", "#!/bin/bash\nexit 0\n")
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 0",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "2",
        "--run-dir", str(tmp_path / "run"),
    )
    assert result.returncode == 0, result.stdout
    assert "STATUS: DONE" in ledger.read_text(encoding="utf-8")


def test_green_suite_without_a_results_table_is_not_done(ledger: Path, tmp_path: Path) -> None:
    """A green suite with no before/after numbers is the fix nobody can check.

    This is exactly what ``PROVEN`` refuses; a runner that accepted it would report
    success for a change whose effect was never measured.
    """
    agent = _script(tmp_path / "ok.sh", "#!/bin/bash\nexit 0\n")
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 0",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "2",
        "--run-dir", str(tmp_path / "run"),
    )
    assert result.returncode == 1, result.stdout
    body = ledger.read_text(encoding="utf-8")
    assert "STATUS: BLOCKED" in body
    assert "unverifiable" in result.stdout


def test_attempt_cap_writes_limit_reached(tmp_path: Path) -> None:
    """``A = 10`` is a terminal status, not a reason to keep passing."""
    ledger = tmp_path / "TASK-999.md"
    attempts = "".join(f"Attempt {n} consumed\n" for n in range(1, 11))
    ledger.write_text(
        "# Bug\n\n## Iteration 1\n\n"
        "FAILING TEST: pytest tests/x.py -> 1 failed\n"
        "BASELINE: 10/10 green\n\n" + attempts,
        encoding="utf-8",
    )
    agent = _script(tmp_path / "noop.sh", "#!/bin/bash\nexit 0\n")
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 1",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "3",
        "--run-dir", str(tmp_path / "run"),
    )
    assert result.returncode == 1
    assert "STATUS: LIMIT_REACHED" in ledger.read_text(encoding="utf-8")


# --- stop conditions ---------------------------------------------------------


def test_cap_stops_the_loop(tmp_path: Path, ledger: Path) -> None:
    """A loop with a moving failure set still stops at the cap."""
    counter = tmp_path / "n"
    agent = _script(
        tmp_path / "noop.sh",
        f'#!/bin/bash\nn=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); '
        f'echo $n > {counter}\n',
    )
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 1",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "2",
        "--run-dir", str(tmp_path / "run"),
    )
    assert result.returncode == 1
    assert "BLOCKED" in ledger.read_text(encoding="utf-8")
    assert counter.read_text(encoding="utf-8").strip() == "2", "the cap must bound passes"


def test_repeated_failures_without_progress_stop_early(tmp_path: Path, ledger: Path) -> None:
    """Two identical failing sets and no ledger change is a stuck loop."""
    agent = _script(tmp_path / "noop.sh", "#!/bin/bash\nexit 0\n")
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "printf 'FAILED tests/a.py::t\\n'; exit 1",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "5",
        "--run-dir", str(tmp_path / "run"),
    )
    assert result.returncode == 1
    assert "no progress" in result.stdout


# --- artifacts and evidence --------------------------------------------------


def test_every_pass_is_recorded(tmp_path: Path, ledger: Path) -> None:
    """A run that ends badly must still be diagnosable."""
    agent = _script(tmp_path / "noop.sh", "#!/bin/bash\nexit 0\n")
    run_dir = tmp_path / "run"
    _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 1",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "2",
        "--run-dir", str(run_dir),
    )
    assert (run_dir / "pass-01" / "prompt.txt").is_file()
    assert (run_dir / "pass-01" / "agent.log").is_file()
    assert (run_dir / "pass-01" / "tests.log").is_file()
    saved = json.loads((run_dir / "pass-01" / "result.json").read_text(encoding="utf-8"))
    assert saved["index"] == 1
    assert saved["test_exit"] == 1

    body = ledger.read_text(encoding="utf-8")
    assert "### F1 — orchestrator pass 1" in body
    assert "| Test exit | `1` |" in body


def test_dry_run_executes_nothing(tmp_path: Path, ledger: Path) -> None:
    """A dry run must not spawn the agent or touch the ledger."""
    marker = tmp_path / "agent-ran"
    agent = _script(tmp_path / "mark.sh", f'#!/bin/bash\ntouch {marker}\n')
    before = ledger.read_text(encoding="utf-8")
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 0",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--dry-run",
    )
    assert result.returncode == 0
    assert not marker.exists(), "dry run must not invoke the agent"
    assert ledger.read_text(encoding="utf-8") == before
    assert "first prompt" in result.stdout


def test_resume_skips_recorded_passes(tmp_path: Path, ledger: Path) -> None:
    """Resume must not repeat work already on disk."""
    agent = _script(tmp_path / "noop.sh", "#!/bin/bash\nexit 0\n")
    run_dir = tmp_path / "run"
    args = (
        "--ledger", str(ledger),
        "--test-cmd", "exit 1",
        "--agent", "command",
        "--agent-cmd", f"{agent} {{prompt}}",
        "--max-passes", "2",
        "--run-dir", str(run_dir),
    )
    _run(*args)
    resumed = _run(*args, "--resume")
    assert "already recorded, skipping" in resumed.stdout


# --- guard rails -------------------------------------------------------------


def test_sudo_in_the_test_command_is_refused(ledger: Path, tmp_path: Path) -> None:
    """The agent runs unprivileged; the harness must not hand it sudo."""
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "sudo make test",
        "--agent", "command",
        "--agent-cmd", "true {prompt}",
        "--dry-run",
    )
    assert result.returncode == 2
    assert "sudo" in result.stderr


def test_sudo_in_the_agent_command_is_refused(ledger: Path) -> None:
    """Same rule for the agent invocations."""
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "make test",
        "--agent", "command",
        "--agent-cmd", "pkexec my-agent {prompt}",
        "--dry-run",
    )
    assert result.returncode == 2
    assert "pkexec" in result.stderr


def test_missing_test_command_fails_closed(ledger: Path) -> None:
    """Guessing the suite command is the defect the gate exists to prevent."""
    result = _run("--ledger", str(ledger), "--test-cmd", "   ", "--dry-run")
    assert result.returncode == 2
    assert "test-cmd is required" in result.stderr


def test_missing_ledger_fails_closed(tmp_path: Path) -> None:
    """A run needs somewhere to write findings."""
    result = _run(
        "--ledger", str(tmp_path / "nope.md"),
        "--test-cmd", "make test",
        "--dry-run",
    )
    assert result.returncode == 2
    assert "no such ledger" in result.stderr


def test_cursor_adapter_requires_a_key(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A key on the command line would land in shell history, so it comes from env only."""
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "make test",
        "--agent", "cursor",
        "--dry-run",
    )
    # The binary may be absent on CI; either refusal is correct, naming the cause.
    assert result.returncode == 2
    assert "CURSOR_API_KEY" in result.stderr or "cursor-agent" in result.stderr


def test_agent_cmd_without_placeholder_is_refused(ledger: Path) -> None:
    """A template that drops the prompt would run the agent on nothing."""
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "make test",
        "--agent", "command",
        "--agent-cmd", "true",
        "--dry-run",
    )
    assert result.returncode == 2
    assert "{prompt}" in result.stderr


def test_phase_prompt_matches_the_observed_phase(ledger: Path) -> None:
    """A stale prompt must not push the agent into a later phase."""
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 0",
        "--agent", "command",
        "--agent-cmd", "true {prompt}",
        "--dry-run",
    )
    assert "PHASE 0/1" in result.stdout


def test_headless_prompt_demands_explicit_assumptions(ledger: Path) -> None:
    """There is no user in a headless run, so ``REPRODUCED`` needs ``Assumption:`` lines.

    The gate refuses ``1→2`` without clarification. A runner that does not say this
    makes the refusal look like a broken pipeline rather than a skipped step.
    """
    result = _run(
        "--ledger", str(ledger),
        "--test-cmd", "exit 0",
        "--agent", "command",
        "--agent-cmd", "true {prompt}",
        "--dry-run",
    )
    prompt = result.stdout
    assert "headless" in prompt.lower()
    assert "Assumption:" in prompt
    assert "Do not skip clarification" in prompt
    assert "no user to ask" in prompt.lower()


def test_probe_prompt_names_the_markers_phase_four_must_write(ledger: Path) -> None:
    """Phase 4 is where the gate is strictest; the prompt has to name what it reads."""
    module = _runner_module()
    prompt = module._prompt_for(4, ledger)
    for marker in ("ФАКТ (RED):", "ФАКТ (логи):", "Attempt <k> consumed", "Secrets scan:",
                   "ФАКТ (GREEN):", "До", "После", "Порог", "Вывод", "Working on:"):
        assert marker in prompt, f"phase 4 prompt never mentions {marker!r}"


def test_every_phase_has_a_prompt(ledger: Path) -> None:
    """A phase without a prompt means the runner drives the agent blind."""
    module = _runner_module()
    for phase in range(0, 6):
        prompt = module._prompt_for(phase, ledger)
        assert "bug-fixer" in prompt
        assert str(ledger) in prompt
        assert "sudo" in prompt, "the unprivileged rule must be in every prompt"
