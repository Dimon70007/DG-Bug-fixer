"""Tests for the hook-enforced bug-fixer gate (TASK-079).

Two layers:

* **Decision** — the rule matrix: the iteration cap, terminal statuses, the
  Phase-1 write ban, and what must stay allowed so a legitimate run is possible.
* **Fail-closed** — unreadable state must deny writes rather than guess, and the
  adapters must carry the harness's fail-closed switch.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_SKILL_DIR = jrnl.SKILL_DIR
_HOOKS = jrnl.HOOKS
_GATE = _HOOKS / "bugfixer_gate.py"
_STATE = _HOOKS / "bugfixer_gate_state.py"


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


@pytest.fixture()
def gate():
    """The gate module."""
    return _load("bugfixer_gate_test", _GATE)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A minimal project: a ledger, a contract with test paths, no sidecar."""
    (tmp_path / "bug-fixer.project.md").write_text(
        "# contract\n\nTest paths: tests/, pkg/tests/\n", encoding="utf-8"
    )
    ledger = tmp_path / "TASK-999.md"
    ledger.write_text("# Bug\n\n## Iteration 1\n", encoding="utf-8")
    return ledger


def _verdict(gate, ledger: Path | None, tool: str, args: dict[str, object]):
    """Run the pure decision function."""
    return gate.decide(ledger=ledger, tool=tool, args=args)


# --- decision matrix ---------------------------------------------------------


def test_phase1_allows_test_writes(gate, project: Path) -> None:
    """Phase 1 exists to write tests; blocking that would make the skill unusable."""
    verdict = _verdict(gate, project, "write", {"file_path": "tests/test_new.py"})
    assert verdict.allow, verdict.reason


def test_phase1_denies_production_writes(gate, project: Path) -> None:
    """The core prohibition: no production change before the cause is confirmed."""
    verdict = _verdict(gate, project, "write", {"file_path": "src/main.py"})
    assert not verdict.allow
    assert "Phase 1" in verdict.reason


def test_phase1_allows_reads(gate, project: Path) -> None:
    """Reading production code is how the cause is found."""
    assert _verdict(gate, project, "read", {"file_path": "src/main.py"}).allow


def test_phase1_allows_running_tests(gate, project: Path) -> None:
    """The baseline run must not be blocked by the gate."""
    assert _verdict(gate, project, "bash", {"command": "pytest tests/ -q"}).allow


def test_phase1_denies_shell_writes(gate, project: Path) -> None:
    """`sed -i` is a production write wearing a shell command's clothes."""
    verdict = _verdict(gate, project, "bash", {"command": "sed -i s/a/b/ src/main.c"})
    assert not verdict.allow


def test_phase1_allows_creating_a_directory_under_the_test_paths(gate, project: Path) -> None:
    """A new test directory is test-tree work; the phase ban is about production code."""
    verdict = _verdict(gate, project, "bash", {"command": "mkdir -p tests/unit/new"})
    assert verdict.allow, verdict.reason


def test_attempt_cap_denies_writes(gate, project: Path) -> None:
    """The cap is ``A`` = 10 probes that reached a code change, not iteration headings.

    The spec replaced the 2.1 ``N``/``M`` counters with `A`/`R<h>` (§8, §16): a counter counted
    from ``## Iteration N`` headings belongs to the scheme that no longer exists. Asserting the
    old marker would keep a dead rule green.
    """
    attempts = "".join(f"Attempt {n} consumed\n" for n in range(1, 11))
    project.write_text("## Task\nfix\n\n" + attempts, encoding="utf-8")
    jrnl.set_phase(project, 4)
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "Attempt cap" in verdict.reason


def test_below_the_attempt_cap_allows_phase4_writes(gate, project: Path) -> None:
    """Nine probes with an active hypothesis still permit the tenth."""
    attempts = "".join(f"Attempt {n} consumed\n" for n in range(1, 10))
    project.write_text(
        "## Task\nfix\n\nWorking on: H1\n\n### H1 — claim\n| Rank | 1 |\n" + attempts,
        encoding="utf-8",
    )
    jrnl.set_phase(project, 4)
    assert _verdict(gate, project, "write", {"file_path": "src/x.py"}).allow


def test_phase4_allows_production_writes_with_an_active_hypothesis(gate, project: Path) -> None:
    """Phase 4 is the only phase where an edit is legal, and it must stay legal."""
    project.write_text(
        "## Iteration 9\n\nWorking on: H1\n\n### H1 — claim\n| Rank | 1 |\n", encoding="utf-8"
    )
    jrnl.set_phase(project, 4)
    assert _verdict(gate, project, "write", {"file_path": "src/x.py"}).allow


def test_phase3_denies_production_writes(gate, project: Path) -> None:
    """Research collects evidence; an edit there is a probe without a hypothesis."""
    project.write_text("## Iteration 2\n\nWorking on: H1\n", encoding="utf-8")
    jrnl.set_phase(project, 3)
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "Phase 3" in verdict.reason


def test_phase4_denies_a_write_without_an_active_hypothesis(gate, project: Path) -> None:
    """Invariant 12: an unattributable edit cannot be a probe."""
    project.write_text("## Iteration 2\n", encoding="utf-8")
    jrnl.set_phase(project, 4)
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "Working on: H" in verdict.reason


def test_phase4_denies_a_write_naming_a_missing_hypothesis(gate, project: Path) -> None:
    """A marker pointing nowhere attributes the edit to nothing."""
    project.write_text("## Iteration 2\n\nWorking on: H9\n\n### H1 — claim\n", encoding="utf-8")
    jrnl.set_phase(project, 4)
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "H9" in verdict.reason


def test_phase4_denies_a_table_form_attempt(gate, project: Path) -> None:
    """Invariant 17b: a marker in a table cell is invisible to the gate."""
    project.write_text(
        "## Iteration 2\n\nWorking on: H1\n\n### H1 — claim\n| Attempt | 3 consumed |\n",
        encoding="utf-8",
    )
    jrnl.set_phase(project, 4)
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "own line" in verdict.reason


def test_blocked_status_denies_even_test_writes(gate, project: Path) -> None:
    """A blocked run must ask the user, not keep editing."""
    project.write_text("## Iteration 2\n\nSTATUS: BLOCKED\n", encoding="utf-8")
    verdict = _verdict(gate, project, "write", {"file_path": "tests/a.py"})
    assert not verdict.allow
    assert "BLOCKED" in verdict.reason


def test_done_status_denies_writes(gate, project: Path) -> None:
    """A finished run must not be reopened by accident."""
    project.write_text("## Iteration 1\n\nSTATUS: DONE\n", encoding="utf-8")
    assert not _verdict(gate, project, "write", {"file_path": "tests/a.py"}).allow


def test_status_mentioned_in_prose_is_not_a_status(gate) -> None:
    """A rules table saying "STATUS: BLOCKED" must not block the task forever."""
    text = "| guard | writes `STATUS: BLOCKED` on the 10th pass |\n## Iteration 1\n"
    assert gate.read_status(text) == "RUNNING"


def test_partial_is_terminal_for_the_automaton(gate, project: Path) -> None:
    """3.0: PARTIAL is a record of experience, not a paused run.

    The remainder and the refuted hypotheses stay in the task, so the next attempt
    is a new ledger that reads this one. Letting writes continue would silently
    finish work nobody is tracking.
    """
    project.write_text("## Iteration 2\n\nSTATUS: PARTIAL\n", encoding="utf-8")
    jrnl.set_phase(project, 2)
    assert gate.read_status(project.read_text(encoding="utf-8")) == "PARTIAL"
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "PARTIAL" in verdict.reason


def test_limit_reached_denies_writes(gate, project: Path) -> None:
    """``A = 10`` has no terminal but this one, so it must actually stop work.

    The refusal names the missing `## Report` first, because step 4.10 writes the status **and**
    the report together (invariant 19) — a cap with no report is a stop nobody can audit. What
    matters here is that the run is over, which both refusals say.
    """
    project.write_text(
        "## Task\nx\n\nSTATUS: LIMIT_REACHED\n\n## Report\nИтог: cap reached\n", encoding="utf-8"
    )
    verdict = _verdict(gate, project, "write", {"file_path": "tests/a.py"})
    assert not verdict.allow
    assert "LIMIT_REACHED" in verdict.reason


def test_unknown_status_is_denied_not_read_as_running(gate, project: Path) -> None:
    """Invariant 15: a status the gate cannot read is not "probably RUNNING"."""
    project.write_text("## Iteration 1\n\nSTATUS: PROBABLY_FINE\n", encoding="utf-8")
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow
    assert "not one of" in verdict.reason


def test_partial_does_not_override_a_terminal_status(gate) -> None:
    """A ledger holding both markers is BLOCKED; the stricter state wins."""
    text = "STATUS: PARTIAL\n\nSTATUS: BLOCKED\n"
    assert gate.read_status(text) == "BLOCKED"


def test_verify_matrix_never_reports_an_unobserved_pass() -> None:
    """The verification script must distinguish absent from verified."""
    script = (_HOOKS / "verify_harnesses.py").read_text(encoding="utf-8")
    for verdict in ("not-installed", "not-automatable", "not-wired"):
        assert verdict in script, f"verify_harnesses.py cannot report {verdict}"
    assert "is NOT a pass" in script, "the matrix does not say absence is not a pass"


def test_verify_script_runs_and_self_tests() -> None:
    """The gate self-test inside the verify script must actually pass."""
    result = subprocess.run(
        [sys.executable, str(_HOOKS / "verify_harnesses.py")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "gate self-test: PASS" in result.stdout


def test_verify_script_finds_the_gate_without_a_parent_layout() -> None:
    """The script must locate the gate from its own tree, not from ``skills/<id>``.

    It used to derive the repository root as ``parents[3]`` and reach back down through
    ``skills/bug-fixer``. In a clone of the plugin — the one layout that has to work — that
    path does not exist, so the self-test ran a gate that was not there and reported a
    broken gate.
    """
    text = (_HOOKS / "verify_harnesses.py").read_text(encoding="utf-8")
    assert "parents[3]" not in text, "the verify script still assumes the parent repository layout"
    assert 'skills", "bug-fixer' not in text and "skills/bug-fixer" not in text, (
        "the verify script still builds a path through the parent's skills/ directory"
    )


def test_the_gate_reads_only_the_project_contract(gate, tmp_path: Path) -> None:
    """One place for the contract: ``bug-fixer.project.md``, as §6 Step 0 says.

    The gate used to also accept a filled ``repo-contract.md`` under
    ``skills/<id>/references`` — the layout of the repository the plugin was written in. That
    path means nothing in another project, and a plugin has to answer the same way wherever
    it is installed, so the second layout is gone.
    """
    ledger = tmp_path / "T.md"
    ledger.write_text("## Iteration 1\n", encoding="utf-8")
    stray = tmp_path / "skills" / "some-skill" / "references"
    stray.mkdir(parents=True)
    (stray / "repo-contract.md").write_text("Test paths: tests/\n", encoding="utf-8")
    assert gate._test_paths(ledger) == [], "a parent-layout contract is still being read"

    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    assert gate._test_paths(ledger) == ["tests/"]


def test_no_ledger_allows_everything(gate) -> None:
    """The gate must be inert outside a bug-fixer run, or it gets uninstalled."""
    assert _verdict(gate, None, "write", {"file_path": "src/x.py"}).allow


# --- invariant 31: the task file is where the work is recorded ---------------
#
# §5.3 puts ``### H<n>`` and ``### F<n>`` in the task file; every step of §6 has a
# "Записать" column pointing at it. Invariant 31 therefore says a write to the task file
# is allowed in *any* phase — a refusal there forbids the work the skill exists to do,
# and the agent cannot argue its way out because the phase never changes on its own.

_PHASES = (1, 2, 3, 4, 5)


def _set_phase(ledger: Path, phase: int) -> None:
    """Put the ledger in a phase, the way the state CLI does."""
    jrnl.set_phase(ledger, phase)


@pytest.mark.parametrize("phase", _PHASES)
def test_the_task_file_may_be_written_in_every_phase(gate, project: Path, phase: int) -> None:
    """Invariant 31: phase 1 records findings just as phase 4 does."""
    _set_phase(project, phase)
    verdict = _verdict(gate, project, "write", {"file_path": str(project)})
    assert verdict.allow, verdict.reason


@pytest.mark.parametrize("tool", ["write", "strreplace"])
def test_the_task_file_is_recognised_whatever_the_tool(gate, project: Path, tool: str) -> None:
    """Both edit tools address the ledger by path; the phase ban is about code, not records."""
    _set_phase(project, 2)
    verdict = _verdict(gate, project, tool, {"file_path": project.name})
    assert verdict.allow, verdict.reason


def test_the_task_file_is_recognised_below_the_project_root(gate, tmp_path: Path) -> None:
    """A ledger under ``tasks/`` arrives as a relative path; both forms name one file."""
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "tasks" / "TASK-999.md"
    ledger.parent.mkdir()
    ledger.write_text("## Iteration 1\n", encoding="utf-8")
    _set_phase(ledger, 3)
    verdict = _verdict(gate, ledger, "write", {"file_path": "tasks/TASK-999.md"})
    assert verdict.allow, verdict.reason


def test_appending_to_the_task_file_from_a_shell_is_allowed(gate, project: Path) -> None:
    """Appending a record is the same write; refusing the redirection would be the same ban."""
    _set_phase(project, 1)
    verdict = _verdict(gate, project, "bash", {"command": f'printf "### F2\\n" >> {project.name}'})
    assert verdict.allow, verdict.reason


def test_a_shell_write_that_also_touches_code_is_still_denied(gate, project: Path) -> None:
    """Invariant 31 opens the ledger, not the code: a command naming both is a code write."""
    _set_phase(project, 1)
    verdict = _verdict(gate, project, "bash", {"command": f"cp {project.name} src/x.py"})
    assert not verdict.allow, "a copy into production code was allowed by the ledger exemption"
    assert "Phase 1" in verdict.reason


def test_a_shell_read_of_the_journal_is_allowed(gate, project: Path) -> None:
    """Invariant 33 forbids writing the journal, not reading it — the agent must be able to."""
    verdict = _verdict(gate, project, "bash", {"command": f"cat {project.name}.bugfixer.jsonl"})
    assert verdict.allow, verdict.reason


def test_a_shell_write_to_a_same_named_file_elsewhere_is_denied(gate, project: Path) -> None:
    """The exemption is by resolved path: another ``TASK-999.md`` is not this run's ledger."""
    _set_phase(project, 1)
    verdict = _verdict(gate, project, "write", {"file_path": "elsewhere/TASK-999.md"})
    assert not verdict.allow, "a path match on the file name alone opened the phase ban"


@pytest.mark.parametrize("phase", (1, 2, 3, 5))
def test_production_writes_stay_denied_outside_phase_4(gate, project: Path, phase: int) -> None:
    """Allowing the ledger must not become allowing everything (invariant 13)."""
    _set_phase(project, phase)
    verdict = _verdict(gate, project, "write", {"file_path": "src/x.py"})
    assert not verdict.allow, "a production edit was allowed outside phase 4"
    assert f"Phase {phase}" in verdict.reason


# --- fail closed -------------------------------------------------------------


def test_corrupt_sidecar_denies(gate, project: Path) -> None:
    """An unreadable phase is not "probably phase 2"."""
    (project.parent / (project.name + ".bugfixer.jsonl")).write_text("not json\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _verdict(gate, project, "write", {"file_path": "tests/a.py"})


def test_missing_test_paths_denies(gate, tmp_path: Path) -> None:
    """Without a contract the gate cannot prove a write is a test edit."""
    ledger = tmp_path / "T.md"
    ledger.write_text("## Iteration 1\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _verdict(gate, ledger, "write", {"file_path": "tests/a.py"})


def test_attempts_counted_from_ledger_not_sidecar(gate, tmp_path: Path) -> None:
    """The ledger is the source of truth for ``A``; the sidecar holds only the phase.

    ``A`` is derived from ``Attempt <k> consumed`` lines (§8). A count kept in the sidecar could
    disagree with the ledger, and then the cap would be enforced against a number nobody can see.
    """
    ledger = tmp_path / "T.md"
    ledger.write_text("Attempt 1 consumed\nAttempt 2 consumed\n", encoding="utf-8")
    assert gate.count_attempts(ledger.read_text(encoding="utf-8")) == 2


# --- no silent skips --------------------------------------------------------
#
# The rule these pin: a library that skips something it does not understand fails
# silently, and a silent failure is one nobody ever reports. During an active run
# an unclassifiable call is denied, so a broken wiring is loud on the first call.


def test_unknown_tool_is_denied_not_allowed(gate, project: Path) -> None:
    """A renamed tool must not read as "not a write".

    Before this, an unknown tool fell through ``_is_write_attempt`` as ``False`` and
    was allowed. A harness renaming ``Write`` would have disabled the gate for the
    whole run, invisibly.
    """
    verdict = _verdict(gate, project, "frobnicate", {"file_path": "src/x.py"})
    assert not verdict.allow, "an unclassifiable tool was allowed during an active run"
    assert "frobnicate" in verdict.reason
    assert "fix the adapter" in verdict.reason


@pytest.mark.parametrize("tool", ["read", "grep", "glob", "webfetch", "todowrite", "task"])
def test_known_read_only_tools_stay_allowed(gate, project: Path, tool: str) -> None:
    """Failing closed must not mean denying reads: the skill is unusable then."""
    assert _verdict(gate, project, tool, {"file_path": "src/x.py"}).allow


def test_shell_tool_without_a_readable_command_is_denied(gate, project: Path) -> None:
    """A shell call whose command cannot be read cannot be proven test-only.

    An adapter that renames ``command`` to something the gate does not know would
    otherwise turn every shell write into an allow.
    """
    verdict = _verdict(gate, project, "bash", {"weird_key": "sed -i s/a/b/ src/main.c"})
    assert not verdict.allow
    assert "no readable command" in verdict.reason


def test_shell_command_under_an_alternate_key_is_read(gate, project: Path) -> None:
    """The new key is recognised rather than denied, so the fix is not just "deny more"."""
    verdict = _verdict(gate, project, "bash", {"command_line": "sed -i s/a/b/ src/main.c"})
    assert not verdict.allow
    assert "Phase 1" in verdict.reason


def test_invalid_stdin_json_denies_instead_of_allowing(tmp_path: Path) -> None:
    """Non-JSON on stdin used to become ``{}``, i.e. "no run active", i.e. allow all.

    A corrupt payload in an active run must fail closed, because the alternative is
    a gate that silently permits every write and looks healthy.
    """
    (tmp_path / "bug-fixer.project.md").write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "T.md"
    ledger.write_text("## Iteration 1\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_GATE), "--ledger", str(ledger)],
        input="{not json",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2, "a corrupt payload was allowed through"
    payload = json.loads(result.stdout)
    assert payload["permission"] == "deny"
    assert "not valid JSON" in payload["agent_message"]


def test_missing_explicit_ledger_denies(tmp_path: Path) -> None:
    """``--ledger`` pointing at nothing is a broken install, not "no run active"."""
    result = subprocess.run(
        [sys.executable, str(_GATE), "--ledger", str(tmp_path / "absent.md")],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/x.py"}}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "does not exist" in json.loads(result.stdout)["agent_message"]


def test_no_ledger_in_payload_still_allows(gate) -> None:
    """The inert-outside-a-run behaviour is deliberate and must survive.

    Without it the gate denies every tool call in every repository and gets
    uninstalled within a day. The distinction is: *no run* allows, *unknown during a
    run* denies.
    """
    assert _verdict(gate, None, "frobnicate", {}).allow


def test_attempt_marker_ignores_prose_and_tables(gate) -> None:
    """A prose mention or a table cell is not an ``Attempt`` line (§4, invariant 17b)."""
    assert gate.count_attempts("we are on Attempt 4, see above") == 0
    assert gate.count_attempts("| Attempt | 4 consumed |") == 0


# --- CLI contract ------------------------------------------------------------


def _run(payload: dict[str, object], ledger: Path) -> subprocess.CompletedProcess[str]:
    """Run the gate the way a harness does."""
    return subprocess.run(
        [sys.executable, str(_GATE), "--ledger", str(ledger)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_exit_codes_and_json(project: Path) -> None:
    """Allow is exit 0 with permission allow; deny is exit 2 with a message."""
    allowed = _run({"tool_name": "Read", "tool_input": {"file_path": "src/x.py"}}, project)
    assert allowed.returncode == 0
    assert json.loads(allowed.stdout)["permission"] == "allow"

    denied = _run({"tool_name": "Write", "tool_input": {"file_path": "src/x.py"}}, project)
    assert denied.returncode == 2
    payload = json.loads(denied.stdout)
    assert payload["permission"] == "deny"
    assert payload["agent_message"]


def test_cli_empty_stdin_does_not_crash(tmp_path: Path) -> None:
    """A harness glitch must not turn into a stack trace."""
    result = subprocess.run(
        [sys.executable, str(_GATE)],
        input="",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["permission"] == "allow"


def test_state_cli_roundtrip(project: Path) -> None:
    """The skill advances the phase through this CLI; the gate must see it.

    The fixture ledger gets a reproduction, a baseline and three clarification
    pairs, so ``REPRODUCED`` is a legal edge; ``--evidence`` is required for every
    transition (TASK-079.1, TASK-081 ST-2).
    """
    with project.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n## Clarifications\n"
            "Q: a  A: b\nQ: c  A: d\nQ: e  A: f\n"
            "\nFAILING TEST: pytest tests/x.py -> 1 failed\nBASELINE: 10/10 green\n"
        )
    out = subprocess.run(
        [sys.executable, str(_STATE), str(project), "advance", "2", "--evidence", "RED + baseline"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    state = json.loads(out.stdout)
    assert state["phase"] == 2
    transitions = [record for record in jrnl.read(project) if record["event"] == "advance"]
    assert transitions[0]["edge"] == "REPRODUCED"
    assert transitions[0]["at"].endswith("Z")


def test_state_cli_rejects_bad_values(project: Path) -> None:
    """Only the five phases and the five statuses are accepted."""
    for args in (("phase", "9"), ("status", "MAYBE")):
        result = subprocess.run(
            [sys.executable, str(_STATE), str(project), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2, args


# --- adapter templates -------------------------------------------------------


def _template(name: str) -> dict:
    """Load an adapter template as JSON."""
    return json.loads((_HOOKS / name).read_text(encoding="utf-8"))


def test_cursor_template_is_fail_closed() -> None:
    """Cursor fails open by default; the template must opt in.

    The template carries no ``__LEDGER__`` and no install-time path: an absolute ledger
    path cannot be known at install time, and the gate discovers it from the project
    contract. The wrapper is referenced through ``$CURSOR_PROJECT_DIR``, which is the
    documented variable for project hooks.
    """
    payload = _template("cursor.hooks.json")
    hook = payload["hooks"]["preToolUse"][0]
    assert hook["failClosed"] is True
    assert "run-gate.sh" in hook["command"]
    assert "CURSOR_PROJECT_DIR" in hook["command"]
    assert "__LEDGER__" not in hook["command"]
    assert "__GATE_WRAPPER__" not in hook["command"]


def test_claude_template_blocks_on_failure() -> None:
    """Exit 1 blocks nothing in Claude Code; onFailure is what makes it safe."""
    payload = _template("claude.settings.json")
    handler = payload["hooks"]["PreToolUse"][0]["hooks"][0]
    assert handler["onFailure"] == "block"
    assert handler["type"] == "command"


def test_codex_template_uses_pretooluse() -> None:
    """Codex reads PreToolUse from hooks.json."""
    payload = _template("codex.hooks.json")
    assert "PreToolUse" in payload["hooks"]


def test_ts_adapters_use_the_right_api() -> None:
    """OpenClaw needs api.on, not registerHook; OpenCode blocks by throwing."""
    opencode = (_HOOKS / "opencode-plugin.ts").read_text(encoding="utf-8")
    openclaw = (_HOOKS / "openclaw-plugin.ts").read_text(encoding="utf-8")

    assert "tool.execute.before" in opencode
    assert "throw new Error" in opencode
    assert "api.on(" in openclaw
    assert "before_tool_call" in openclaw
    # The comment explains why registerHook is wrong; only a real call is a defect.
    assert "api.registerHook(" not in openclaw, "registerHook never runs typed tool hooks"


def _code_lines(text: str) -> str:
    """Strip block and line comments so a check inspects code, not prose."""
    without_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(
        line for line in without_block.splitlines() if not line.strip().startswith("//")
    )


def test_ts_adapters_do_not_depend_on_bun() -> None:
    """F1: OpenClaw is a Node program, so a Bun global is not guaranteed.

    A ReferenceError inside a gate reads to the agent as "the guard is broken",
    which is the one failure mode that must never be ambiguous.
    """
    for name in ("opencode-plugin.ts", "openclaw-plugin.ts"):
        code = _code_lines((_HOOKS / name).read_text(encoding="utf-8"))
        assert "Bun." not in code, f"{name} uses the Bun runtime"
        assert "node:child_process" in code, f"{name} does not spawn via node:child_process"
        assert "spawn(" in code


def test_ts_adapters_deny_on_spawn_failure() -> None:
    """A gate that cannot start must block, not allow."""
    for name in ("opencode-plugin.ts", "openclaw-plugin.ts"):
        text = (_HOOKS / name).read_text(encoding="utf-8")
        assert 'child.on("error"' in text, f"{name} does not handle a spawn error"
        # The error branch must produce a deny/block, not fall through to allow.
        error_block = text.split('child.on("error"')[1].split("})")[0]
        assert "allow: false" in error_block or "block: true" in error_block


def test_shell_hook_templates_quote_every_placeholder() -> None:
    """F2: an unquoted path breaks on the first path with a space.

    Only placeholders the template actually uses are checked. ``__LEDGER__`` is gone
    from the Cursor template on purpose — it is discovered, not substituted — and
    Claude/Codex templates still substitute their own values.
    """
    for name in ("cursor.hooks.json", "claude.settings.json", "codex.hooks.json"):
        text = (_HOOKS / name).read_text(encoding="utf-8")
        parsed = json.dumps(json.loads(text))
        for placeholder in ("__PYTHON__", "__GATE__", "__LEDGER__"):
            if placeholder not in text and placeholder not in parsed:
                continue
            assert f'\\"{placeholder}\\"' in text or f'"{placeholder}"' in parsed, (
                f"{name} leaves {placeholder} unquoted"
            )


def test_cursor_template_does_not_narrow_the_matcher() -> None:
    """F3: a partial tool list is a silent bypass, which is worse than a spawn.

    Cursor's tool vocabulary is Write|StrReplace|Delete|Shell|CallMcpTool|... and
    the docs do not publish an exhaustive enum. Omitting the matcher runs the gate
    on every call and lets the gate decide; a matcher that is missing one tool
    name would let that tool through unguarded.
    """
    payload = _template("cursor.hooks.json")
    hook = payload["hooks"]["preToolUse"][0]
    assert "matcher" not in hook, (
        "a narrowed matcher risks silently skipping a write tool; "
        "the gate already classifies calls itself"
    )
    assert hook["failClosed"] is True


def test_adapters_document_the_subagent_gap() -> None:
    """The bypass must be visible where someone reads the adapter."""
    opencode = (_HOOKS / "opencode-plugin.ts").read_text(encoding="utf-8")
    assert "subagent" in opencode.lower()
