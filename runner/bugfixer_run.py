#!/usr/bin/env python3
"""Drive a coding agent through the bug-fixer phases (TASK-080).

The gate in ``skills/bug-fixer/hooks/`` polices what an agent may do inside a
pass. This script polices whether the loop continues at all, and it decides that
on evidence the agent cannot fake: the test runner's exit code and the failing
test ids.

    bugfixer_run.py --ledger tasks/TASK-1.md --test-cmd "make test" --agent cursor
    bugfixer_run.py ... --dry-run
    bugfixer_run.py ... --agent command --agent-cmd 'fake-agent {prompt}'

Design note on why the two controls are separate: an agent can write
``STATUS: DONE`` in prose, so "done" is never read from its output. Only a green
suite with the Phase-1 RED test still passing ends a run as ``DONE``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Sequence

#: This file lives at <skill>/runner/bugfixer_run.py, so the skill directory is two up.
SKILL_DIR: Final[Path] = Path(__file__).resolve().parent.parent
STATE_CLI: Final[Path] = SKILL_DIR / "hooks" / "bugfixer_gate_state.py"

#: Commands that must never appear: the agent runs unprivileged, per repo rule 6.2.
_FORBIDDEN_CMDS: Final[tuple[str, ...]] = ("sudo", "su", "doas", "pkexec")

#: How long one agent pass may take before it is treated as a failed pass.
_DEFAULT_PASS_TIMEOUT_SEC: Final[int] = 20 * 60

#: Cap on captured output per stream, so a runaway agent cannot fill the disk.
_MAX_CAPTURE_BYTES: Final[int] = 4 * 1024 * 1024


@dataclass
class PassResult:
    """Outcome of one agent + test pass.

    Attributes:
        index: 1-based pass number.
        phase: Phase the pass ran in, as observed before it started.
        agent_exit: Agent process exit code, or ``None`` when it timed out.
        test_exit: Test command exit code, or ``None`` when not run.
        failing: Sorted ids of failing tests, used for the no-progress check.
        changed: Whether the ledger changed during the pass.
        prompt_sha: Short hash of the prompt, for the record.
    """

    index: int
    phase: int
    agent_exit: int | None
    test_exit: int | None
    failing: list[str] = field(default_factory=list)
    changed: bool = False
    prompt_sha: str = ""


@dataclass(frozen=True)
class Plan:
    """Resolved run configuration.

    Attributes:
        ledger: Ledger file for this bug.
        test_cmd: Command that decides success.
        agent: Adapter id.
        agent_cmd: Template for the ``command`` adapter.
        max_passes: Hard cap on passes.
        run_dir: Where artifacts go.
        pass_timeout: Seconds allowed per agent pass.
    """

    ledger: Path
    test_cmd: str
    agent: str
    agent_cmd: str
    max_passes: int
    run_dir: Path
    pass_timeout: int
    workdir: Path


def _fail(message: str) -> "NoReturn":  # noqa: F821 - typing only
    """Abort with a message that says how to fix the cause."""
    print(f"bugfixer-run: {message}", file=sys.stderr)
    raise SystemExit(2)


def _contains_forbidden(command: str) -> str | None:
    """Return the forbidden privileged command found in ``command``, if any.

    Args:
        command: Shell command line to inspect.

    Returns:
        The offending token, or ``None``.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        base = token.rsplit("/", 1)[-1]
        if base in _FORBIDDEN_CMDS:
            return base
    return None


def _stamp() -> str:
    """UTC timestamp for a run directory."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _current_phase(ledger: Path) -> int:
    """Read the phase from the state CLI, defaulting to 1.

    Args:
        ledger: Ledger file.

    Returns:
        Phase number.
    """
    result = subprocess.run(
        [sys.executable, str(STATE_CLI), str(ledger)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return 1
    try:
        return int(json.loads(result.stdout).get("phase") or 1)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 1


def _ledger_status(ledger: Path) -> str:
    """Read a terminal status from the ledger, using the gate's anchoring rule.

    Args:
        ledger: Ledger file.

    Returns:
        ``BLOCKED``, ``DONE``, ``PARTIAL``, or ``RUNNING``.
    """
    text = ledger.read_text(encoding="utf-8", errors="replace")
    found = {
        match.group(1)
        for match in re.finditer(
            r"^[ \t>*_-]{0,8}STATUS:[ \t]*(BLOCKED|DONE|PARTIAL)\b", text, re.MULTILINE
        )
    }
    for status in ("BLOCKED", "DONE", "PARTIAL"):
        if status in found:
            return status
    return "RUNNING"


_FAILING_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:FAILED|FAILED tests/|ERROR)\s+(\S+)", re.MULTILINE
)


def _failing_ids(output: str) -> list[str]:
    """Extract failing test ids from common runner output.

    Args:
        output: Combined stdout and stderr of the test command.

    Returns:
        Sorted, de-duplicated ids.
    """
    ids = {match.group(1).rstrip(":") for match in _FAILING_RE.finditer(output)}
    return sorted(ids)


def _prompt_for(phase: int, ledger: Path) -> str:
    """Build the bounded prompt for one pass.

    Args:
        phase: Phase the run is in, as observed from the sidecar.
        ledger: Ledger file the agent must write into.

    Returns:
        Prompt text.
    """
    common = (
        f"You are running the bug-fixer skill on {ledger}.\n"
        "Read SKILL.md and the project contract before acting.\n"
        "Do not use sudo. Do not edit production code outside the current phase's rules.\n"
    )
    if phase == 1:
        return common + (
            "PHASE 1 (reproduce). Write the failing test that reproduces the bug and run it. "
            "Run the full suite unchanged to capture the baseline. Then append to the ledger, "
            "each on its own line: `FAILING TEST: <command> -> <observed>`, "
            "`BASELINE: <pass>/<total>, already red: <ids>`."
        )
    if phase == 2:
        return common + (
            "PHASE 2 (hypothesise & probe). Rank at least two hypotheses. For each, run one "
            "discriminating experiment and record the finding as an `F<n>` block with `Fact` and "
            "a `**Verdict.**` line. Only after a verdict says CONFIRMED may you change code."
        )
    return common + (
        "PHASE 3 (decide). Rerun the full suite plus the Phase-1 failing test. Compare against "
        "the baseline. Do not claim success: the orchestrator runs the tests itself. If the work "
        "is incomplete and the remainder is listable, write `## Remaining` and `STATUS: PARTIAL`."
    )


def _run_cmd(
    command: Sequence[str] | str,
    *,
    cwd: Path,
    timeout: int,
    shell: bool,
    env: dict[str, str] | None = None,
) -> tuple[int | None, str]:
    """Run a command, capturing output with a size cap.

    Args:
        command: argv list, or a shell string.
        cwd: Working directory.
        timeout: Seconds allowed.
        shell: Whether to run through the shell.
        env: Environment for the child; defaults to the parent's.

    Returns:
        ``(exit_code, combined_output)``; exit code is ``None`` on timeout.
    """
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=shell,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None, f"TIMEOUT after {timeout}s"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output[-_MAX_CAPTURE_BYTES:]


def _agent_argv(plan: Plan, prompt: str) -> list[str]:
    """Build the agent invocation for one pass.

    Args:
        plan: Resolved configuration.
        prompt: Prompt text.

    Returns:
        argv list.
    """
    if plan.agent == "cursor":
        return [
            "cursor-agent",
            "-p",
            "--force",
            "--output-format",
            "text",
            prompt,
        ]
    if "{prompt}" not in plan.agent_cmd:
        _fail("--agent-cmd must contain {prompt}")
    return shlex.split(plan.agent_cmd.replace("{prompt}", shlex.quote(prompt)))


def _agent_env() -> dict[str, str]:
    """Environment for the agent pass.

    The API-key prerequisite is checked in ``_resolve_plan`` so a dry run reports
    it too; this only returns the environment the pass will run with.

    Returns:
        Environment mapping.
    """
    return dict(os.environ)


def _advance(ledger: Path, target: int, evidence: str) -> bool:
    """Ask the FSM to advance, respecting its guards.

    Args:
        ledger: Ledger file.
        target: Destination phase.
        evidence: Why the transition is justified.

    Returns:
        ``True`` when the transition succeeded.
    """
    result = subprocess.run(
        [sys.executable, str(STATE_CLI), str(ledger), "advance", str(target), "--evidence", evidence],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"bugfixer-run: transition refused: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def _append_finding(ledger: Path, result: PassResult) -> None:
    """Append one ``F<n>`` block describing the pass.

    Args:
        ledger: Ledger file.
        result: Pass outcome.
    """
    text = ledger.read_text(encoding="utf-8", errors="replace")
    numbers = [int(m) for m in re.findall(r"^###\s+F(\d+)\b", text, re.MULTILINE)]
    index = (max(numbers) + 1) if numbers else 1
    failing = ", ".join(result.failing) if result.failing else "none"
    block = (
        f"\n### F{index} — orchestrator pass {result.index} (phase {result.phase})\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Iteration | `N` = {result.index} |\n"
        f"| Agent exit | `{result.agent_exit}` |\n"
        f"| Test exit | `{result.test_exit}` |\n"
        f"| Failing | {failing} |\n"
        f"| Ledger changed | {result.changed} |\n"
        f"| Prompt sha | `{result.prompt_sha}` |\n"
    )
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(block)


def _write_status(ledger: Path, status: str) -> None:
    """Write the terminal status line, replacing any previous one.

    Args:
        ledger: Ledger file.
        status: ``DONE`` or ``BLOCKED``.
    """
    text = ledger.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"^[ \t>*_-]{0,8}STATUS:.*$\n?", "", text, flags=re.MULTILINE)
    with ledger.open("w", encoding="utf-8") as handle:
        handle.write(text.rstrip("\n") + f"\n\nSTATUS: {status}\n")


def _resolve_plan(args: argparse.Namespace) -> Plan:
    """Validate arguments and return a resolved plan.

    Args:
        args: Parsed arguments.

    Returns:
        Populated :class:`Plan`.

    Raises:
        SystemExit: On any missing or forbidden value.
    """
    ledger = Path(args.ledger).expanduser()
    if not ledger.is_file():
        _fail(f"no such ledger: {ledger}. Create the task file first; a run needs a place to write findings.")

    if not args.test_cmd.strip():
        _fail(
            "--test-cmd is required. Guessing the full-suite command is the defect the gate exists "
            "to prevent; pass e.g. --test-cmd 'make test'."
        )

    for candidate in (args.test_cmd, args.agent_cmd or ""):
        found = _contains_forbidden(candidate)
        if found:
            _fail(
                f"refusing a command containing `{found}`: the agent runs unprivileged (repo rule 6.2). "
                "Run the privileged step yourself, outside the loop."
            )

    if args.max_passes < 1:
        _fail("--max-passes must be at least 1")

    if args.agent == "cursor" and shutil.which("cursor-agent") is None:
        _fail("--agent cursor needs `cursor-agent` on PATH; install the Cursor CLI or use --agent command")

    if args.agent == "command" and not args.agent_cmd:
        _fail("--agent command needs --agent-cmd with a {prompt} placeholder")

    if args.agent == "command" and "{prompt}" not in args.agent_cmd:
        _fail("--agent-cmd must contain {prompt}; without it the agent would run on no task")

    # Validated here so a dry run catches every missing prerequisite too: a dry run
    # that passes on a configuration the real run would refuse is a lie.
    if args.agent == "cursor" and not os.environ.get("CURSOR_API_KEY"):
        _fail(
            "the cursor adapter needs CURSOR_API_KEY. Export it first:\n"
            "  export CURSOR_API_KEY=...   # create one in the Cursor dashboard\n"
            "A key is never accepted on the command line: it would land in shell history."
        )

    # The agent and the test command must run where the code is, not where the skill
    # lives. Default: the ledger's project, i.e. the ledger's parent directory.
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else ledger.parent.resolve()
    if not workdir.is_dir():
        _fail(f"--workdir {workdir} is not a directory")

    run_dir = Path(args.run_dir).expanduser() if args.run_dir else Path("logs") / f"bugfixer-run-{_stamp()}"

    return Plan(
        ledger=ledger,
        test_cmd=args.test_cmd,
        agent=args.agent,
        agent_cmd=args.agent_cmd or "",
        max_passes=args.max_passes,
        run_dir=run_dir,
        pass_timeout=args.pass_timeout,
        workdir=workdir,
    )


def run(plan: Plan, *, dry_run: bool, resume: bool) -> int:
    """Execute the loop.

    Args:
        plan: Resolved configuration.
        dry_run: Print the plan without executing anything.
        resume: Skip passes already recorded in the run directory.

    Returns:
        Process exit code: ``0`` only when the run ended ``DONE``.
    """
    if os.geteuid() == 0:
        _fail("refusing to run as root; the agent would inherit full privileges (repo rule 6.2)")

    ledger_before = plan.ledger.read_text(encoding="utf-8", errors="replace")
    prompt_preview = _prompt_for(_current_phase(plan.ledger), plan.ledger)

    if dry_run:
        print(f"ledger:      {plan.ledger}")
        print(f"test command: {plan.test_cmd}")
        print(f"agent:       {plan.agent}")
        print(f"max passes:  {plan.max_passes}")
        print(f"run dir:     {plan.run_dir}")
        print(f"phase now:   {_current_phase(plan.ledger)}")
        print(f"status now:  {_ledger_status(plan.ledger)}")
        print("\nfirst prompt:\n" + prompt_preview)
        return 0

    plan.run_dir.mkdir(parents=True, exist_ok=True)
    if (plan.run_dir / "plan.json").exists() and not resume:
        _fail(f"{plan.run_dir} already holds a run; pass --resume or choose another --run-dir")

    previous_failing: list[str] | None = None
    done = False

    for index in range(1, plan.max_passes + 1):
        artifact = plan.run_dir / f"pass-{index:02d}"
        if resume and (artifact / "result.json").is_file():
            print(f"==> pass {index}: already recorded, skipping")
            saved = json.loads((artifact / "result.json").read_text(encoding="utf-8"))
            previous_failing = saved.get("failing")
            if saved.get("test_exit") == 0:
                done = True
                break
            continue

        status = _ledger_status(plan.ledger)
        if status in {"DONE", "BLOCKED"}:
            print(f"==> ledger already {status}; stopping")
            done = status == "DONE"
            break

        phase = _current_phase(plan.ledger)
        prompt = _prompt_for(phase, plan.ledger)
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "prompt.txt").write_text(prompt, encoding="utf-8")

        print(f"==> pass {index} (phase {phase}): agent")
        agent_exit, agent_output = _run_cmd(
            _agent_argv(plan, prompt),
            cwd=plan.workdir,
            timeout=plan.pass_timeout,
            shell=False,
            env=_agent_env(),
        )
        (artifact / "agent.log").write_text(agent_output, encoding="utf-8")

        print(f"==> pass {index}: tests (`{plan.test_cmd}`)")
        test_exit, test_output = _run_cmd(
            plan.test_cmd,
            cwd=plan.workdir,
            timeout=plan.pass_timeout,
            shell=True,
        )
        (artifact / "tests.log").write_text(test_output, encoding="utf-8")

        ledger_after = plan.ledger.read_text(encoding="utf-8", errors="replace")
        failing = _failing_ids(test_output)
        result = PassResult(
            index=index,
            phase=phase,
            agent_exit=agent_exit,
            test_exit=test_exit,
            failing=failing,
            changed=ledger_after != ledger_before,
            prompt_sha=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
        )
        (artifact / "result.json").write_text(json.dumps(result.__dict__, indent=2) + "\n", encoding="utf-8")

        _append_finding(plan.ledger, result)
        ledger_before = plan.ledger.read_text(encoding="utf-8", errors="replace")

        if test_exit == 0:
            print(f"==> pass {index}: suite green — DONE")
            _write_status(plan.ledger, "DONE")
            done = True
            break

        if previous_failing is not None and failing == previous_failing and not result.changed:
            print(f"==> pass {index}: same failures and no ledger change — no progress, stopping")
            _write_status(plan.ledger, "BLOCKED")
            break
        previous_failing = failing

        # Advance the phase only when its guard agrees; never force it.
        if phase == 1 and "BASELINE:" in ledger_after and "FAILING TEST:" in ledger_after:
            _advance(plan.ledger, 2, f"pass {index}: reproduction and baseline recorded")
        elif phase == 2 and re.search(r"\bCONFIRMED\b", ledger_after):
            _advance(plan.ledger, 3, f"pass {index}: a hypothesis is confirmed")

    else:
        print(f"==> cap reached ({plan.max_passes} passes) without a green suite")
        _write_status(plan.ledger, "BLOCKED")

    print(f"==> run directory: {plan.run_dir}")
    return 0 if done else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="bugfixer_run.py",
        description="Drive a coding agent through the bug-fixer phases, stopping on evidence.",
    )
    parser.add_argument("--ledger", required=True, help="task file the agent writes findings into")
    parser.add_argument("--test-cmd", required=True, help="full-suite command; its exit code decides")
    parser.add_argument("--agent", choices=("cursor", "command"), default="cursor")
    parser.add_argument("--agent-cmd", default="", help="template for --agent command; must contain {prompt}")
    parser.add_argument("--max-passes", type=int, default=5)
    parser.add_argument("--pass-timeout", type=int, default=_DEFAULT_PASS_TIMEOUT_SEC)
    parser.add_argument("--run-dir", default="")
    parser.add_argument(
        "--workdir",
        default="",
        help="where the agent and the test command run; defaults to the ledger's directory",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    plan = _resolve_plan(args)
    return run(plan, dry_run=args.dry_run, resume=args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
