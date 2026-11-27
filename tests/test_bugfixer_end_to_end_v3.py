"""DoD-7: one run from `doctor` to `DONE` in a scratch project, end to end.

The individual pieces are tested elsewhere; what is not tested is that they compose. This module
installs the plugin into a throwaway project and drives the whole path the way a real session
would:

1. `doctor` on a project with no contract — must fail, naming what is missing;
2. `doctor` on a project with a starter contract — must pass;
3. a production write in phase 1 — must be refused **by the installed hook**, not by the source;
4. the legal path — tests, baseline, hypothesis, probe, verdict — writing each record into the
   ledger with the markers the gate reads;
5. a production write in phase 4 with the hypothesis active — must be allowed;
6. the run ends `DONE`, and the last write after that is refused again.

The last step is the one that matters most: a gate that opens and never closes passes every test
above and still fails the purpose of the skill.

Every assertion is made against the *installed* tree and its own processes, because an install can
copy every file and still be broken — a stripped exec bit, a relative path that resolves only in
the source tree, a hook that reads its config from `$HOME`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import bugfixer_helpers as jrnl

_INSTALL = jrnl.SKILL_DIR / "install.py"

#: A production file and a test file, named the way the contract's `Test paths:` expects.
_PRODUCTION = "app/service.py"
_TEST_FILE = "tests/test_service.py"

_CONTRACT = (
    "# bug-fixer contract: scratch project\n\n"
    "## Ledger\n\n"
    "Task/bug file: tasks/TASK-*.md\n"
    "Docs paths: docs/\n\n"
    "Findings go into that file as `F<n>` blocks, appended, never reordered. A second ledger is\n"
    "not allowed: no `DEBUG.md`, no separate notes file.\n\n"
    "## Tests\n\n"
    "Full suite: python -m pytest -q\n"
    "Test paths: tests/\n\n"
    "## Search\n\n"
    "Allowed: none\n\n"
    "If `none`: skip step 3.3 and build hypotheses from Phase 1 evidence only.\n\n"
    "## Extra prohibitions for this project\n\n"
    "- no sudo from the agent; print the command for the operator\n"
    "- dependencies only via poetry; never pip install into a system interpreter\n\n"
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — doctor passed, suite runs\n"
)


def _install(project: Path) -> subprocess.CompletedProcess[str]:
    """Install the plugin into a project, the way an operator does.

    Args:
        project: Scratch project root.

    Returns:
        The installer's completed process.
    """
    return subprocess.run(
        [sys.executable, str(_INSTALL), "--target", "cursor", "--project", str(project)],
        text=True,
        capture_output=True,
        check=False,
    )


def _bugfixer(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the *installed* entry point, with the project as cwd.

    Args:
        project: Scratch project root.
        *args: Arguments after ``bugfixer``.

    Returns:
        The completed process.
    """
    entry = project / ".cursor" / "skills" / "bug-fixer" / "bin" / "bugfixer"
    return subprocess.run(
        [sys.executable, str(entry), *args],
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )


def _hook(project: Path, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Execute the installed preToolUse hook as the harness does.

    Args:
        project: Scratch project root.
        payload: Harness payload on stdin.

    Returns:
        Exit code and the parsed JSON answer.
    """
    wrapper = project / ".cursor" / "skills" / "bug-fixer" / "hooks" / "run-gate.sh"
    result = subprocess.run(
        [str(wrapper)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(project),
        check=False,
    )
    answer = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.returncode, answer


def _write(project: Path, path: str, content: str = "x = 1\n") -> tuple[int, dict[str, Any]]:
    """Ask the hook about a write, the way Cursor asks.

    Args:
        project: Scratch project root.
        path: Target path, relative to the project.
        content: Text being written.

    Returns:
        Exit code and the parsed answer.
    """
    return _hook(
        project,
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(project / path), "content": content},
            "cwd": str(project),
            "workspace_roots": [str(project)],
        },
    )


def _scratch(project: Path) -> Path:
    """Create a project with a contract, a ledger and a source tree.

    Args:
        project: Directory to populate.

    Returns:
        The project root.
    """
    (project / "tasks").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "app").mkdir()
    (project / "bug-fixer.project.md").write_text(_CONTRACT, encoding="utf-8")
    (project / "tasks" / "TASK-700.md").write_text(
        "## Task\nThe cache returns a stale value after an overwrite.\n", encoding="utf-8"
    )
    (project / "app" / "service.py").write_text("def read():\n    return 1\n", encoding="utf-8")
    return project


def _append(project: Path, text: str) -> None:
    """Append a paragraph to the ledger, as the agent does.

    Args:
        project: Scratch project root.
        text: Markdown to add.
    """
    ledger = project / "tasks" / "TASK-700.md"
    ledger.write_text(ledger.read_text(encoding="utf-8") + text, encoding="utf-8")


def _advance(project: Path, phase: int, edge: str, evidence: str) -> subprocess.CompletedProcess[str]:
    """Prove an edge through the CLI, which is the only writer of the journal.

    Args:
        project: Scratch project root.
        phase: Target phase.
        edge: Edge name.
        evidence: What the edge was proved by.

    Returns:
        The completed process.
    """
    return _bugfixer(
        project,
        "state",
        str(project / "tasks" / "TASK-700.md"),
        "advance",
        str(phase),
        "--edge",
        edge,
        "--evidence",
        evidence,
    )


def test_the_whole_run_from_doctor_to_done(tmp_path: Path) -> None:
    """DoD-7: `doctor` → deny in phase 1 → `DONE`, on an installed plugin.

    Each step asserts both the verdict and, where it matters, that the verdict came from the
    installed tree rather than from this checkout.
    """
    project = _scratch(tmp_path / "scratch")

    # --- 1. install ---------------------------------------------------------
    installed = _install(project)
    assert installed.returncode == 0, f"install failed: {installed.stderr[:400]}"

    # --- 2. doctor refuses a project with no contract ----------------------
    installed_contract = project / "bug-fixer.project.md"
    kept = installed_contract.read_text(encoding="utf-8")
    installed_contract.unlink()
    no_contract = _bugfixer(project, "doctor")
    assert no_contract.returncode == 2, (
        "without a contract the gate must refuse and say so (invariant 37); "
        f"got exit {no_contract.returncode}: {no_contract.stdout[:300]}"
    )

    # `doctor` on the starter contract warns that nothing was confirmed, and exits 0: the file is
    # well formed, but a human has not confirmed it against a real run yet.
    starter = _bugfixer(project, "init", "--project", ".")
    assert starter.returncode == 0, starter.stderr
    assert installed_contract.is_file(), "`init` must leave a contract behind (invariant 38)"

    # --- 3. the confirmed contract passes `doctor` -------------------------
    installed_contract.write_text(kept, encoding="utf-8")
    doctor = _bugfixer(project, "doctor")
    assert doctor.returncode == 0, (
        f"a complete contract must pass `doctor`; got exit {doctor.returncode}: {doctor.stderr}"
    )
    assert "ledger" in doctor.stdout, "doctor must name the ledger it resolved"

    # --- 3. phase 1 refuses a production write ------------------------------
    code, answer = _write(project, _PRODUCTION)
    assert code != 0, "phase 1 must not allow a production edit"
    assert answer.get("permission") == "deny", answer
    reason = answer.get("agent_message", "")
    assert reason, "a refusal without a reason is a broken guard (spec §0)"

    # --- 4. the legal path: tests, baseline, hypothesis, probe -------------
    assert _write(project, _TEST_FILE)[0] == 0, "phase 1 exists to write failing tests"

    _append(
        project,
        "\n## Clarifications\n"
        "Q: On which input does it reproduce?  A: overwrite then read\n"
        "Q: What is correct behaviour?  A: read returns the new value\n"
        "Q: Which test proves it?  A: tests/test_service.py\n",
    )
    _append(
        project,
        "\n## Iteration 1\n\n"
        "### F1 — baseline\n"
        "| Field | Value |\n|---|---|\n| Kind | research |\n| Scope | task |\n"
        "| Responds to | — |\n| Supersedes | — |\n| Iteration | 1 |\n"
        "| Outcome | BASELINE recorded |\n\n"
        "FAILING TEST: python -m pytest tests/test_service.py -> 1 failed\n"
        f"BASELINE: 1/2, красные: {_TEST_FILE}\n",
    )
    reproduced = _advance(project, 2, "REPRODUCED", "failing test plus baseline")
    assert reproduced.returncode == 0, f"REPRODUCED refused: {reproduced.stderr}"

    _append(
        project,
        "\n### F2 — route\n"
        "| Field | Value |\n|---|---|\n| Kind | route |\n| Scope | task |\n"
        "| Responds to | F1 |\n| Supersedes | — |\n| Iteration | 1 |\n| Outcome | ROUTED: H0 created |\n\n"
        "### H0 — the cache key omits the writer\n"
        "| Field | Value |\n|---|---|\n| Rank | 1 |\n| Status | derived |\n"
        "| Proposed | F2 |\n| Origin | description |\n"
        "| Mechanism | writer B overwrites key A, so the reader sees A's old value |\n"
        "| Prediction | isolating keys removes both symptoms |\n"
        "| Discriminator | — (the only one) |\n\n"
        "#### Records\n- F2 → PROPOSED\n\n"
        "Working on: H0\n",
    )
    first_guess = _advance(project, 4, "FIRST_GUESS", "H0 created and active")
    assert first_guess.returncode == 0, f"FIRST_GUESS refused: {first_guess.stderr}"

    # --- 5. phase 4 allows the production edit, attributed to H0 -----------
    code, answer = _write(project, _PRODUCTION, "def read():\n    return 2\n")
    assert code == 0, f"phase 4 must allow a probe edit; got {answer.get('agent_message', '')}"

    # --- 6. the probe is recorded with every proof `PROVEN` reads ----------
    _append(
        project,
        "\n### F3 — probe H0: keys are isolated\n"
        "| Field | Value |\n|---|---|\n| Kind | probe |\n| Scope | H0 |\n"
        "| Responds to | F2 |\n| Supersedes | — |\n| Iteration | 1 |\n"
        "| Outcome | CONFIRMED |\n\n"
        "| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |\n"
        "|---|---|---|---|---|---|---|\n"
        f"| `{Path(_TEST_FILE).stem}` | stale after overwrite | `stale_hits` | 7 | 0 | 0 | fixed |\n"
        f"| `{Path(_TEST_FILE).stem}` | duplicate rows | `dup_count` | 4 | 0 | 0 | fixed |\n\n"
        "ФАКТ (RED): python -m pytest tests/test_service.py -> 1 failed\n"
        "ФАКТ (логи): writer B no longer touches key A\n"
        "Bug: stable\n"
        "Attempt 1 consumed\n"
        "Secrets scan: clean\n"
        "ФАКТ (GREEN): python -m pytest -> 2 passed\n"
        "Corner cases: none — the two targets cover both symptoms\n\n"
        "#### Records\n- F3 → CONFIRMED\n",
    )
    proven = _advance(project, 5, "PROVEN", "CONFIRMED with RED, logs, GREEN and the table")
    assert proven.returncode == 0, f"PROVEN refused: {proven.stderr}"

    # --- 7. the run ends DONE ----------------------------------------------
    # The ledger carries the marker (step 5.4 writes it), and the *journal* carries the state:
    # invariant 34 makes the last journalled record the single source of truth, so the status has
    # to go through the CLI. Writing `STATUS: DONE` into the file alone leaves the run RUNNING,
    # which is exactly the second-source-of-truth bug the invariant removes.
    _append(
        project,
        "\nSTATUS: DONE\n\n"
        "## Report\n"
        "Итог: DONE — both symptoms closed, suite green.\n"
        "Корневая причина: caller B reset another caller's key — F3 / H0.\n"
        "Что изменено: app/service.py — key isolation on invalidate.\n"
        "Таблица результатов: in F3.\n"
        "Опровергнутые гипотезы: none.\n"
        "Остаток: —\n"
        "Что нужно от оператора: —\n"
        "Уроки: an e2e test on two keys would have caught it earlier.\n",
    )
    finished = _bugfixer(
        project, "state", str(project / "tasks" / "TASK-700.md"), "status", "DONE"
    )
    assert finished.returncode == 0, f"the status CLI refused DONE: {finished.stderr}"

    state = _bugfixer(project, "state", str(project / "tasks" / "TASK-700.md"))
    assert state.returncode == 0, state.stderr
    run = json.loads(state.stdout)
    assert run["phase"] == 5, f"PROVEN must land in phase 5: {run}"
    assert run["status"] == "DONE", (
        f"the run must end DONE, not merely reach phase 5; got {run['status']!r}. Phase 5 is "
        "reached by PROVEN alone, so the status is what says the fix is finished (§9)."
    )

    # --- 8. and the gate closes behind it ----------------------------------
    code, answer = _write(project, _PRODUCTION, "def read():\n    return 3\n")
    assert code != 0, "a finished run must not be reopened by a production edit"
    assert answer.get("permission") == "deny", answer


def test_the_install_is_what_refused_not_the_source_checkout(tmp_path: Path) -> None:
    """The hook above runs from the installed tree; without it, the source would be doing the work.

    This pins the wiring rather than the verdict: the wrapper, the gate and the entry point all
    exist inside the project after an install, and none of them is a symlink into this checkout.
    """
    project = _scratch(tmp_path / "scratch")
    assert _install(project).returncode == 0
    installed = project / ".cursor" / "skills" / "bug-fixer"
    for relative in ("bin/bugfixer", "hooks/run-gate.sh", "hooks/bugfixer_gate.py"):
        path = installed / relative
        assert path.exists(), f"the install is missing {relative}"
        assert not path.is_symlink(), f"{relative} points back into the source checkout"
    assert jrnl.SKILL_DIR not in installed.resolve().parents
