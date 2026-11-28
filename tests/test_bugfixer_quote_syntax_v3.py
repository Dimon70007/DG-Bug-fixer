"""Quotes are text, not syntax — except when they carry a command (spec §5.5, F12/F22/F23).

The gate classified a shell call by scanning the raw command string. Shell metacharacters
inside quotes therefore counted as syntax: `grep -rn "a|mkdir" logs/` looked like a pipeline
into `mkdir`, and `python3 -c "print('a -> b')"` looked like a redirection. Measured on one
session: **96 of 3312 commands** (2.9%) were refused as production writes while doing nothing
but reading (TASK-086 F22).

The fix is not "ignore quotes" — that was measured too, and it dropped 111 commands that DO
write, because a quoted `-c` argument is itself a command and a heredoc body is itself a
program (F23). Both shapes stay refusals here, and the tests below prove it.

Every test was run against the previous code first: the ones marked "was red" failed there.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_GATE = helpers.HOOKS / "bugfixer_gate.py"


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
    """The gate module under test."""
    return _load("bugfixer_gate_quotes", _GATE)


#: Read-only commands whose text contains a metacharacter inside quotes. Each was refused
#: before the change; each is harmless.
READ_ONLY = [
    'grep -rn "chdir|mkdir" logs/',
    'grep -n "^| P[0-9] | 10a" file.md',
    "rg 'x>>y' docs/",
    'rg "a|mkdir" hooks/',
    'echo "=== with Test paths -> ALLOW ==="',
    "python3 -c \"print('a -> b')\"",
    'awk \'NR>=1 && NR<=5{printf "%d|%s\\n", NR, $0}\' file.md',
    'sed -n \'s/a|b/c/d/p\' file.txt',
    'echo "rm -rf build"',
    'echo "> out.txt"',
    "printf '%s|%s\\n' a b",
]

#: Commands that really write. None may be lost by the change.
REAL_WRITES = [
    "echo hi > out.txt",
    "cat a.txt > b.txt",
    "pytest -q 2> errors.log",
    "tee build.log",
    "cp src/a.py tests/b.py",
    "mkdir -p logs/dumps",
    "rm -rf build",
    "touch src/x.py",
    "sed -i s/a/b/ src/main.c",
    "git commit -m 'msg'",
    "bash -c 'echo hi > f.txt'",
    'sh -c "cat >> x <<EOF"',
    "bash -c \"echo hi > f.txt\"",
    "python3 - <<'PY'\nimport pathlib\nPY",
    "./.venv/bin/python - <<'PY'\nimport pathlib\nPY",
    "/usr/bin/python3 - <<'PY'\nprint(1)\nPY",
]


@pytest.mark.parametrize("command", READ_ONLY)
def test_a_read_only_command_with_quoted_metacharacters_is_not_a_write(gate, command: str) -> None:
    """The quoted span is an argument; a shell would pass it through untouched.

    Was red for every entry: the raw-string scan saw `>`, `>>` or `|<verb>` inside the quotes.
    """
    is_write, _ = gate._is_write_attempt("shell", {"command": command})
    assert is_write is False, f"{command!r} was classified as a write"


@pytest.mark.parametrize("command", REAL_WRITES)
def test_a_real_write_is_still_a_write(gate, command: str) -> None:
    """The negative control: nothing that writes may be reclassified as a read.

    The last three were red before the change as well — a heredoc into an interpreter was
    treated as a read, which is a bypass the change closes rather than opens (F23).
    """
    is_write, _ = gate._is_write_attempt("shell", {"command": command})
    assert is_write is True, f"{command!r} was NOT classified as a write"


@pytest.mark.parametrize(
    "command",
    [
        'grep -rn "a|mkdir" logs/',
        "rg 'x>>y' docs/",
        'echo "a > b"',
        "python3 -c \"print('a -> b')\"",
    ],
)
def test_a_quoted_metacharacter_yields_no_phantom_target(gate, command: str) -> None:
    """The refusal message names targets; it must not invent one from quoted text.

    Before the change `grep "chdir|mkdir"` produced the targets `'"'` and `'logs/'` — the
    first is not a path at all, and a message naming it is unreadable.
    """
    targets = gate._shell_write_targets(command)
    assert targets == [], f"phantom targets: {targets}"


def test_a_quoted_path_is_extracted_without_its_quotes(gate) -> None:
    """A real write target keeps working when quoted; the quoting is stripped, not the path."""
    targets = gate._shell_write_targets('rm -f "src/my file.py"')
    assert targets == ["src/my file.py"], targets


def test_a_nested_command_still_writes(gate) -> None:
    """`bash -c '…'` executes its argument, so the quoted text is syntax there.

    This is the case that makes "just ignore quotes" a bypass; it was measured dropping 111
    real writes in one session (F23).
    """
    is_write, _ = gate._is_write_attempt(
        "shell", {"command": "bash -c 'echo hi > /outside-a/f.txt'"}
    )
    assert is_write is True


def test_a_heredoc_into_an_interpreter_still_writes(gate) -> None:
    """A heredoc body fed to python is a program, whatever the body happens to contain."""
    is_write, _ = gate._is_write_attempt(
        "shell", {"command": "python3 - <<'PY'\nimport pathlib\nPY"}
    )
    assert is_write is True


def test_a_heredoc_into_cat_is_not_a_write_by_itself(gate) -> None:
    """A heredoc into `cat` without a redirection writes nothing: `cat` reads until EOF.

    `cat > f <<'EOF'` writes, because of the redirection; `cat <<'EOF'` only prints.
    """
    is_write, _ = gate._is_write_attempt("shell", {"command": "cat <<'EOF'\nsome text\nEOF"})
    assert is_write is False


def test_a_nested_print_of_a_redirect_is_refused(gate) -> None:
    """Documented limitation, not an accident: `python3 -c` text is scanned as shell.

    A quote-insensitive classifier cannot tell `print('a > b')` from `open('f','w')` without
    parsing Python, so everything inside a `-c` argument is treated as capable of writing.

    The consequence is a false refusal for a command that only prints. It is accepted because
    the alternative was measured and worse: ignoring `-c` arguments opened a bypass of 111
    commands in one session (TASK-086 F23). Recorded here so the trade-off is visible rather
    than discovered again.
    """
    is_write, _ = gate._is_write_attempt(
        "shell", {"command": "python3 -c \"print('cat >> x <<EOF')\""}
    )
    assert is_write is True


def test_the_suite_command_is_still_allowed(gate, tmp_path: Path) -> None:
    """The baseline step must keep working — it is the reason the discard exemption exists."""
    contract = tmp_path / "bug-fixer.project.md"
    contract.write_text("Test paths: tests/\n", encoding="utf-8")
    ledger = tmp_path / "TASK-1.md"
    ledger.write_text("## Task\nx\n", encoding="utf-8")
    verdict = gate.decide(ledger=ledger, tool="shell", args={"command": "pytest -q 2>/dev/null"})
    assert verdict.allow, verdict.reason
