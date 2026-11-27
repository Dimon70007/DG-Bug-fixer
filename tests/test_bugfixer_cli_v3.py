"""One entry point: `bugfixer gate|state|secrets` (TASK-085 DoD-4, 4a, 4b).

Stage 3 of TASK-085 promised a single command and shipped two of its five verbs. The measured
consequences, all reproduced before this file existed:

* `bugfixer --help` listed `{init,doctor}`, so `gate`, `state` and `secrets` — the three things a
  run actually calls — had no supported way in;
* `SKILL.md` (7 places) and `README.md` (7) taught `python3 hooks/bugfixer_gate_state.py …`, a path
  that resolves only when the working directory is the plugin's, which it never is: the agent runs
  from the project;
* the gate's own refusals taught the same broken command, so the one text a blocked agent reads was
  the one text that did not work.

The tests below check behaviour, not spelling. "The command named in a refusal exists" is asserted
by taking the path out of the message and looking for it on disk — a rename that leaves the prose
looking right still fails.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_SKILL = jrnl.SKILL_DIR
_HOOKS = jrnl.HOOKS
_CLI = _SKILL / "bin" / "bugfixer"
_GATE = _HOOKS / "bugfixer_gate.py"

CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\nRaw dumps: logs/dumps/\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)

LEDGER = (
    "## Task\nfix\n\n## Clarifications\nQ: a  A: b\nQ: c  A: d\nQ: e  A: f\n\n"
    "FAILING TEST: pytest -> 1 failed\nBASELINE: 5/5\n"
)


def _load(name: str, path: Path, loader: object | None = None):
    """Import a module from a file path, registering it for dataclass lookup.

    ``bin/bugfixer`` has no `.py` suffix, so the default loader refuses it; the caller that needs
    it supplies a source loader explicitly rather than renaming the entry point.
    """
    if loader is None:
        spec = importlib.util.spec_from_file_location(name, path)
    else:
        spec = importlib.util.spec_from_loader(name, loader)
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
def project(tmp_path: Path) -> Path:
    """A project in phase 1 with a contract and a ledger."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
    ledger = tmp_path / "tasks" / "TASK-1.md"
    ledger.write_text(LEDGER, encoding="utf-8")
    jrnl.set_phase(ledger, 1)
    return ledger


def _cli(*args: str, cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run `bin/bugfixer` the way the agent would: from the project, by absolute path."""
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        cwd=str(cwd),
    )


def _hook(script: Path, *args: str, cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run one of the hook scripts directly, as the old instructions taught."""
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        cwd=str(cwd),
    )


def _payload(path: str, project: Path) -> str:
    """A Write payload naming ``path``, in the shape the gate reads."""
    return json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "contents": "x"},
            "cwd": str(project),
            "workspace_roots": [str(project)],
        }
    )


# --- DoD-4: the verbs exist --------------------------------------------------


def test_the_entry_point_offers_every_verb_a_run_calls(tmp_path: Path) -> None:
    """`--help` is the inventory: a verb missing here has no supported way in."""
    result = _cli("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    for verb in ("init", "doctor", "gate", "state", "secrets", "hooks"):
        assert verb in result.stdout, f"`bugfixer {verb}` is not offered"


def test_the_verifier_is_reachable_and_still_reports_honestly(tmp_path: Path) -> None:
    """`README` sends an operator here to check the wiring; a missing verb breaks that step.

    The self-test row is asserted, not the per-harness table: which harnesses are installed is a
    property of the machine, and this test must pass on one with none of them.
    """
    result = _cli("hooks", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "gate self-test: PASS" in result.stdout
    assert "verified denies:" in result.stdout


@pytest.mark.parametrize(
    "target",
    ["src/app.py", "tests/test_app.py", "README.md"],
)
def test_gate_returns_the_same_verdict_as_the_gate_script(
    project: Path, tmp_path: Path, target: str
) -> None:
    """DoD-4's regression: the same payload must produce the same decision.

    Three targets, one allowed by the phase-1 ban and two refused by it, so an always-deny or
    always-allow delegate cannot pass.
    """
    payload = _payload(target, project.parent)
    direct = _hook(_GATE, cwd=project.parent, stdin=payload)
    through_cli = _cli("gate", cwd=project.parent, stdin=payload)
    assert through_cli.returncode == direct.returncode
    assert json.loads(through_cli.stdout) == json.loads(direct.stdout)


def test_gate_accepts_the_explicit_ledger_and_dev_plugin_flags(project: Path) -> None:
    """Both flags exist on the script; a wrapper that drops them changes the verdict."""
    payload = _payload("skills/bug-fixer/lib/x.py", project.parent)
    refused = _cli("gate", cwd=project.parent, stdin=payload)
    assert refused.returncode == 2
    allowed = _cli("gate", "--dev-plugin", "--ledger", str(project), cwd=project.parent, stdin=payload)
    assert allowed.returncode == 0, allowed.stdout


def test_state_advances_the_run_through_the_cli(project: Path) -> None:
    """`bugfixer state` must own the journal, or the phase stays in the script nobody calls."""
    result = _cli(
        "state", str(project), "advance", "2", "--evidence", "RED + baseline", cwd=project.parent
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["phase"] == 2
    advances = [record for record in jrnl.read(project) if record["event"] == "advance"]
    assert advances and advances[-1]["to"] == 2


def test_state_lists_the_edges_and_reads_the_state(project: Path) -> None:
    """Reading the run and listing the edges are the two calls the skill names most often."""
    edges = _cli("state", str(project), "edges", cwd=project.parent)
    assert edges.returncode == 0, edges.stderr
    assert "FIRST_GUESS" in edges.stdout
    state = _cli("state", str(project), cwd=project.parent)
    assert json.loads(state.stdout)["phase"] == 1


def test_state_refuses_a_value_outside_the_machine(project: Path) -> None:
    """A refusal is exit `2`; a wrapper that swallows it would look like success.

    Paired with the accepted value, because `2` alone is also what a missing verb returns —
    the refusal has to be the state machine's, not argparse's.
    """
    accepted = _cli("state", str(project), "status", "BLOCKED", cwd=project.parent)
    assert accepted.returncode == 0, accepted.stderr
    refused = _cli("state", str(project), "status", "MAYBE", cwd=project.parent)
    assert refused.returncode == 2
    assert "status must be one of" in refused.stderr


def test_secrets_scans_a_file_and_the_diff(tmp_path: Path) -> None:
    """Step 4.6a names this call; it has to be reachable, and to keep its exit codes."""
    leaked = tmp_path / "leak.txt"
    leaked.write_text("token: ghp_" + "a" * 30 + "\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("token: null\n", encoding="utf-8")

    assert _cli("secrets", "--text", str(leaked), cwd=tmp_path).returncode == 2
    assert _cli("secrets", "--text", str(clean), cwd=tmp_path).returncode == 0


def test_a_broken_plugin_tree_is_a_clear_refusal_not_a_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing hook module is an install defect; the agent must read that, not a stack trace.

    Run in-process against the real entry point with one verb repointed at a script that is not
    there: a copy of the tree exercises the same code under a different path, and lines counted
    for a copy say nothing about the file the plugin ships.
    """
    module = _load(
        "bugfixer_cli_broken_tree",
        _CLI,
        loader=importlib.machinery.SourceFileLoader("bugfixer_cli_broken_tree", str(_CLI)),
    )
    monkeypatch.setitem(module._HOOK_MODULES, "gate", "definitely_not_here.py")

    with pytest.raises(SystemExit) as excinfo:
        module.main(["gate"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "is missing; the plugin tree is incomplete" in captured.err


def test_a_project_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    """`--project` naming a file must be refused by name, not crash on `is_dir()`."""
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    for verb in ("init", "doctor"):
        result = _cli(verb, "--project", str(not_a_dir), cwd=tmp_path)
        assert result.returncode == 2, verb
        assert "is not a directory" in result.stderr, verb


def test_the_wrapper_forwards_gate_flags() -> None:
    """`--dev-plugin` is documented, so the wrapper a harness calls must be able to pass it.

    Measured need: developing this plugin with its own gate installed requires the flag, and the
    wrapper dropped it — `exec python3 <gate>` with no `"$@"`. The only way round was to point the
    hook at the Python file directly, which bypasses the wrapper every real install uses.
    """
    script = (_HOOKS / "run-gate.sh").read_text(encoding="utf-8")
    assert '"$@"' in script, "run-gate.sh does not forward its arguments to the gate"


def test_the_wrapper_still_passes_the_exit_code_through() -> None:
    """A wrapper that swallows `2` turns a refusal into a permission."""
    script = (_HOOKS / "run-gate.sh").read_text(encoding="utf-8")
    assert "exec python3" in script, "the wrapper no longer execs the gate directly"
    # `set -u` and `pipefail` are fine; `set -e` is not, and the guard is the exec that follows.
    assert "set -e\n" not in script and "set -e;" not in script, (
        "`set -e` would abort on the gate's exit 2 before the code reaches the harness"
    )


# --- DoD-4b: a refusal teaches a command that runs ---------------------------


def _named_commands(message: str) -> list[str]:
    """Absolute paths to an executable that a refusal tells the agent to run.

    Args:
        message: Refusal text.

    Returns:
        Paths, in message order, deduplicated.
    """
    found = re.findall(r"(/[^\s`]*bin/bugfixer)", message)
    return list(dict.fromkeys(found))


def test_the_phase_one_refusal_names_a_command_that_exists(project: Path) -> None:
    """The agent reads this text while blocked; a path that resolves nowhere wastes the turn.

    The check is executable: the path is taken out of the message and looked for on disk, so
    renaming the script fails here even if the sentence still looks plausible.
    """
    result = _cli("gate", cwd=project.parent, stdin=_payload("src/app.py", project.parent))
    assert result.returncode == 2
    message = json.loads(result.stdout)["agent_message"]
    commands = _named_commands(message)
    assert commands, f"the refusal names no runnable command: {message}"
    for command in commands:
        named = Path(command)
        assert named.is_file(), f"the refusal names {command}, which does not exist"
        assert named.stat().st_mode & 0o100, f"{command} is named but not executable"
    assert " state " in message, "the refusal does not name the state verb it is asking for"
    assert "bugfixer_gate_state.py" not in message, (
        "the refusal still teaches the script path, which resolves only inside the plugin"
    )


def test_the_journal_refusal_names_the_plugin_as_the_writer_and_a_runnable_command(
    project: Path,
) -> None:
    """Invariant 33's refusal is read by an agent that just tried to write history."""
    journal = project.parent / (project.name + ".bugfixer.jsonl")
    payload = _payload(str(journal), project.parent)
    result = _cli("gate", cwd=project.parent, stdin=payload)
    assert result.returncode == 2
    message = json.loads(result.stdout)["agent_message"]
    assert "bugfixer" in message and "not by the agent" in message
    for command in _named_commands(message):
        assert Path(command).is_file(), f"the refusal names {command}, which does not exist"
    assert "bugfixer_gate_state.py" not in message


# --- invariant 47: `--dev-plugin` covers shell writes too ---------------------


def _load_gate():
    """The gate module, imported as the plugin ships it."""
    return _load("bugfixer_gate_cli_v3", _GATE)


def test_dev_plugin_allows_a_shell_write_inside_the_plugin_tree(project: Path) -> None:
    """Invariant 47 names the tree, not the tool that changes it.

    Measured defect: `--dev-plugin` allowed a `Write` to the plugin and refused `rm` of the same
    file, because the plugin marker was searched for in the whole command line while the write
    targets were already extracted separately. The flag is the documented way to develop the
    plugin with its own gate installed, so a shell command that only touches that tree must pass.
    """
    gate = _load_gate()
    verdict = gate.decide(
        ledger=project,
        tool="shell",
        args={"command": "rm -f skills/bug-fixer/lib/bugfixer/probe.py"},
        dev_plugin=True,
    )
    assert verdict.allow, verdict.reason


def test_dev_plugin_still_refuses_a_shell_write_to_project_code(project: Path) -> None:
    """The neighbour case: a flag that opens the tree must not open the project."""
    gate = _load_gate()
    verdict = gate.decide(
        ledger=project,
        tool="shell",
        args={"command": "cp skills/bug-fixer/hooks/x.py src/app.py"},
        dev_plugin=True,
    )
    assert not verdict.allow


def test_without_the_flag_a_shell_write_to_the_plugin_is_refused(project: Path) -> None:
    """Invariant 47's default: during a run the plugin tree is code like any other."""
    gate = _load_gate()
    verdict = gate.decide(
        ledger=project,
        tool="shell",
        args={"command": "rm -f skills/bug-fixer/lib/bugfixer/probe.py"},
    )
    assert not verdict.allow


# --- DoD-4a: the documentation teaches only commands that run ----------------

_DOCS = ("SKILL.md", "README.md")


@pytest.mark.parametrize("name", _DOCS)
def test_the_documentation_carries_no_plugin_relative_script_path(name: str) -> None:
    """`python3 hooks/…` resolves only when cwd is the plugin; the agent's cwd is the project."""
    text = (_SKILL / name).read_text(encoding="utf-8")
    for pattern in (
        "python3 hooks/",
        "hooks/bugfixer_gate_state.py",
        "hooks/bugfixer_secrets.py",
        "hooks/verify_harnesses.py",
    ):
        assert pattern not in text, f"{name} still teaches {pattern!r}, which does not run"


@pytest.mark.parametrize("name", _DOCS)
def test_every_command_the_documentation_names_is_a_verb_of_the_entry_point(
    name: str, tmp_path: Path
) -> None:
    """A doc that invents a verb sends the agent into an argparse error at the worst moment."""
    text = (_SKILL / name).read_text(encoding="utf-8")
    named = set(re.findall(r"`bugfixer ([a-z][a-z-]*)", text))
    named |= set(re.findall(r"(?m)^bugfixer ([a-z][a-z-]*)", text))
    assert named, f"{name} names no `bugfixer` command at all"
    help_text = _cli("--help", cwd=tmp_path).stdout
    missing = sorted(verb for verb in named if verb not in help_text)
    assert not missing, f"{name} names verb(s) the entry point does not offer: {missing}"


def test_the_documentation_names_the_gate_and_the_state_machine_through_the_cli() -> None:
    """Both files must mention the two verbs a run depends on, or their absence is invisible."""
    skill = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
    readme = (_SKILL / "README.md").read_text(encoding="utf-8")
    assert "`bugfixer state" in skill, "SKILL.md does not teach the state verb"
    assert "`bugfixer secrets" in skill, "SKILL.md does not teach the secrets verb"
    assert "`bugfixer state" in readme, "README.md does not teach the state verb"
