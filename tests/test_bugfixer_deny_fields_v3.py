"""A refusal must be explainable from the journal alone (spec §5.4, invariant 36).

Before this module the record carried `at`, `event`, `why` and `tool`: it said *which tool*
was refused, never *what* the tool was pointed at, in *which phase*, under *which rule*, and
in *whose journal*. Measured consequence (TASK-086 F5/F10/F24): 111 of 133 refusals in two
real journals cannot be adjudicated at all, so no decision about tightening or loosening a
rule can be based on them.

Every test here is written to FAIL against the code before TASK-087. The ones that pass on
the old code are noted as such, because a test that cannot fail proves only that green
exists.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as helpers

_SKILL = helpers.SKILL_DIR
_GATE = helpers.HOOKS / "bugfixer_gate.py"
_JOURNAL = helpers.HOOKS / "bugfixer_journal.py"

BASELINE = (
    "## Task\nfix the thing\n\n## Clarifications\n"
    "Q: a  A: b\nQ: c  A: d\nQ: e  A: f\n"
    "\nFAILING TEST: pytest tests/x.py -> 1 failed\nBASELINE: 10/10 green\n"
)


def _load(name: str, path: Path):
    """Import a module from a file path, registering it for dataclass lookup.

    Args:
        name: Module name to register.
        path: File to import.

    Returns:
        The loaded module.
    """
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
    return _load("bugfixer_gate_deny_fields", _GATE)


@pytest.fixture()
def journal():
    """The journal module under test."""
    return _load("bugfixer_journal_deny_fields", _JOURNAL)


@pytest.fixture()
def secrets_module():
    """The scanner, for the masking mechanics behind the journal field."""
    return _load("bugfixer_secrets_deny_fields", helpers.HOOKS / "bugfixer_secrets.py")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project with a contract, a ledger and a source tree.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        Path of the ledger.
    """
    (tmp_path / "bug-fixer.project.md").write_text(
        "Contract version: 1\nContract verified by: test\n\n"
        "## Ledger\n\nTask/bug file: TASK-*.md\nRaw dumps: logs/dumps/\n\n"
        "## Tests\n\nFull suite: pytest\nTest paths: tests/\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "logs" / "dumps").mkdir(parents=True)
    (tmp_path / "src" / "service.py").write_text("x = 1\n", encoding="utf-8")
    ledger = tmp_path / "TASK-999.md"
    ledger.write_text(BASELINE, encoding="utf-8")
    return ledger


def _denials(ledger: Path) -> list[dict]:
    """Denial records from the ledger's journal.

    Args:
        ledger: Task file.

    Returns:
        Records whose ``event`` is ``deny``.
    """
    path = helpers.journal_path(ledger)
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == "deny"
    ]


# --- target: what the tool was pointed at ------------------------------------


def test_a_shell_denial_records_the_command(gate, project: Path) -> None:
    """`tool: shell` alone cannot distinguish a heredoc into the ledger from a source edit.

    This is the field F10 names as the one that makes 51 shell refusals unreadable. The call
    line is recorded verbatim — redirection and destination included — so the refusal says
    *what* was aimed at *where*.
    """
    command = "cat >> src/service.py <<'EOF'\ny = 2\nEOF"
    verdict = gate.decide(ledger=project, tool="shell", args={"command": command})
    assert not verdict.allow
    record = _denials(project)[0]
    assert "cat >> src/service.py <<'EOF'" in record.get("target", ""), (
        f"call line missing or altered: {record.get('target')!r}"
    )


def test_a_write_denial_records_the_path(gate, project: Path) -> None:
    """A write tool carries its destination in the payload; the record must keep it."""
    verdict = gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    assert not verdict.allow
    record = _denials(project)[0]
    assert record.get("target") == "src/service.py"


def test_a_multiline_command_stays_one_record(gate, project: Path) -> None:
    """A heredoc has newlines; the journal is JSON Lines, so the record must not split.

    The `why` field is folded for the same reason. `target` keeps its newlines because JSON
    escapes them, and the round-trip has to be exact or the command cannot be replayed: the
    call line, the body summary and the terminator come back as three separate lines.
    """
    command = "cat > src/service.py <<'EOF'\nline one\nline two\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    path = helpers.journal_path(project)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1, "a record was split by the newlines inside the command"
    target = json.loads(lines[0])["target"]
    assert target.splitlines()[0] == "cat > src/service.py <<'EOF'"
    assert target.splitlines()[-1] == "EOF"


def test_a_long_command_is_recorded_in_full(gate, project: Path) -> None:
    """A long command is a command: it is recorded whole, and reads back byte-for-byte.

    This test used to assert the opposite — a 400-character cap and a ``truncated`` flag
    (TASK-086 ST-10, operator decision 2026-11-28). The cap was withdrawn because a field cut
    at 400 characters answers "what was attempted" as badly as a missing one: the call line can
    be seen, the point of it cannot.

    The body is not part of the call, so it becomes a size; the argument list, which is the
    call, is not shortened at all — that is the second half of this test.
    """
    command = "cat > src/service.py <<'EOF'\n" + ("line of text\n" * 500) + "EOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    record = _denials(project)[0]
    assert "truncated" not in record, "the truncation flag must be gone, not merely unused"
    target = record["target"]
    assert target.startswith("cat > src/service.py <<'EOF'\n"), target[:60]
    assert "[body: 500 lines, 6500 bytes]" in target, target
    assert target.endswith("\nEOF"), target[-40:]
    # The summary replaces 500 lines with one, so the field is small *because* the body is a
    # body — not because the call was cut.
    assert "line of text" not in target


def test_a_long_argument_list_is_not_shortened(gate, project: Path) -> None:
    """Length that comes from arguments is length of the call, so nothing is dropped.

    A heredoc body is a payload; an argument list is the invocation itself. The cap was
    withdrawn to keep exactly this: after 8 000 characters of arguments, the tail — which is
    usually where the interesting flag is — must still be readable.
    """
    arguments = " ".join(f"arg{number}" for number in range(1200))
    command = f"python3 script.py {arguments} > src/service.py"
    assert len(command) > 8000, f"the fixture is too short to prove the point: {len(command)}"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    record = _denials(project)[0]
    assert record["target"] == command, (
        f"a call of {len(command)} characters was cut to {len(record['target'])}"
    )
    assert "truncated" not in record


def test_a_quoted_shift_is_not_a_heredoc(gate, project: Path) -> None:
    """`<<` inside quotes is text, so nothing after it becomes a body to summarise.

    `grep "x << y" file` searches for a string; treating it as an opener would eat the rest of
    the command looking for a terminator that does not exist. The same trap the gate already
    documents for `>` (invariant 45a), in the place where the journal is written.
    """
    command = 'grep "a << b" > src/service.py\necho done'
    gate.decide(ledger=project, tool="shell", args={"command": command})
    assert _denials(project)[0]["target"] == command


def test_an_unterminated_body_is_not_summarised(gate, project: Path) -> None:
    """A heredoc with no terminator has no body; the rest of the command is kept verbatim.

    Summarising it would silently drop whatever followed the `<<`, which is the failure this
    sub-task exists to remove — a shorter field that no longer says what happened.
    """
    command = "cat > src/service.py <<'EOF'\nline one\nline two"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    assert _denials(project)[0]["target"] == command


def test_the_terminator_must_match_exactly(gate, project: Path) -> None:
    """Indented text equal to the delimiter does not close a `<<` heredoc.

    Only `<<-` strips leading tabs. Accepting `  EOF` as a terminator would end the body early
    and hand the rest of the body into the record as if it were part of the call.
    """
    command = "cat > src/service.py <<'EOF'\n  EOF\nstill body\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    target = _denials(project)[0]["target"]
    assert "[body: 2 lines, 17 bytes]" in target, target
    assert "still body" not in target


# --- secrets in the target ---------------------------------------------------


def test_a_secret_in_the_target_is_masked_where_it_sits(gate, project: Path) -> None:
    """§10 forbids the value; it never asked for the command around it (TASK-086 ST-12).

    The command has to be a WRITE for a record to exist at all, so the credential rides in a
    command that appends to a source file — a shape that really happens when an agent writes a
    config. Two facts are asserted, and the second is the one this sub-task exists for: the value
    never reaches the file, and the rest of the call is still there to read.
    """
    value = "abcdefghijklmnop"
    command = f"printf 'AUTHORIZATION: Bearer {value}\\n' >> src/service.py"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    text = helpers.journal_path(project).read_text(encoding="utf-8")
    assert value not in text, "the credential value reached the journal"
    target = _denials(project)[0]["target"]
    assert "<redacted: bearer>" in target, target
    assert target.startswith("printf 'AUTHORIZATION:"), target
    assert target.endswith(">> src/service.py"), target


def test_an_ordinary_long_path_is_not_masked(gate, project: Path) -> None:
    """A path is not a credential, and the `base64` rule cannot tell them apart.

    Its pattern is `[A-Za-z0-9+/]{40,}` and `/` is in that alphabet, so any absolute path
    longer than 40 characters matches. Masking it would leave the field saying nothing,
    which is the opposite of what it is for. Measured on a real temp path (TASK-086 F25).
    """
    long_path = str(project / "src" / "service.py")
    assert len(long_path) > 40, "the fixture path is too short to prove the point"
    gate.decide(ledger=project, tool="write", args={"file_path": long_path})
    record = _denials(project)[0]
    assert record["target"] == long_path, record["target"]


def test_a_token_inside_a_heredoc_body_never_reaches_the_journal(gate, project: Path) -> None:
    """A body is not recorded, so a value in it is not a journal leak — and needs no mask.

    The order in ``_journal_target`` is deliberate: the body becomes a size *before* the scan, so
    the scanner judges the text that will be written. A token that lived only in the body is then
    absent for a better reason than masking — the body was never data the journal keeps (ST-10).
    """
    fake_github_token = "ghp_" + "a" * 30
    command = f"cat > src/service.py <<'EOF'\n{fake_github_token}\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    record = _denials(project)[0]
    assert fake_github_token not in helpers.journal_path(project).read_text(encoding="utf-8")
    assert fake_github_token not in record["target"]
    assert record["target"].startswith("cat > src/service.py <<'EOF'"), record["target"]
    assert "[body: 1 lines," in record["target"], record["target"]


def test_a_token_on_the_call_line_is_masked_in_place(gate, project: Path) -> None:
    """The same shape, the token moved out of the body: now it is journalled, so it is masked."""
    fake_github_token = "ghp_" + "a" * 30
    command = f"cat > src/service.py <<'EOF' # {fake_github_token}\ny = 2\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    record = _denials(project)[0]
    assert fake_github_token not in helpers.journal_path(project).read_text(encoding="utf-8")
    assert record["target"].startswith("cat > src/service.py <<'EOF' # <redacted: github>"), (
        record["target"]
    )


def test_masking_a_fragment_keeps_every_other_argument(gate, project: Path) -> None:
    """The field answers "what was attempted"; a masked word must not erase the attempt.

    Measured before ST-12 (`probes/st12_target_masking.py`): a four-argument command whose
    second argument carried a token was recorded as ``<redacted: assignment>`` — one field, no
    command. The argument order, the program and the destination are the answer, and only the
    value is §10's business.
    """
    value = "hunter2hunter2"
    command = (
        f'mysql --defaults-extra-file=/dev/null -e "password={value}" db >> src/service.py'
    )
    gate.decide(ledger=project, tool="shell", args={"command": command})
    target = _denials(project)[0]["target"]
    assert value not in target, target
    assert "<redacted: assignment>" in target, target
    assert target.startswith("mysql --defaults-extra-file=/dev/null -e"), target
    assert target.endswith('db >> src/service.py'), target


def test_a_secret_in_a_heredoc_body_leaves_the_body_a_size(gate, project: Path) -> None:
    """Two rules meet here and must not fight: §10 masks the value, ST-10 summarises the body.

    Masking the fragment happens on the call line, which is recorded whole; the body is data and
    is replaced by its size before the scan runs — either way the record must not contain the
    body's text, the value, or one word in place of the command.
    """
    value = "abcdefghijklmnop"
    command = "cat >> src/service.py <<'EOF'\nAUTHORIZATION: Bearer " + value + "\nEOF"
    gate.decide(ledger=project, tool="shell", args={"command": command})
    target = _denials(project)[0]["target"]
    assert value not in target, target
    assert target.startswith("cat >> src/service.py <<'EOF'"), target
    assert "[body: 1 lines," in target, target
    assert target.endswith("\nEOF"), target


def test_two_secrets_on_one_line_are_both_masked(gate, project: Path) -> None:
    """`scan` reports one finding per line, so one pass leaves the second credential written.

    Measured (`probes/st12_target_masking.py`, case `two-secrets-one-line`): a `ghp_…` token
    survived beside a masked `sk-…` token on the same command line. The leak was introduced by
    fragment masking itself — the old field-wide replacement could not have it, because it
    erased everything. Both values must be gone and the call still readable.
    """
    github = "ghp_" + "a" * 30
    openai = "sk-" + "b" * 22
    command = f'printf %s "{github} {openai}" >> src/service.py'
    gate.decide(ledger=project, tool="shell", args={"command": command})
    target = _denials(project)[0]["target"]
    assert github not in target, target
    assert openai not in target, target
    assert target.count("<redacted: ") == 2, target
    assert target.startswith('printf %s "'), target
    assert target.endswith('" >> src/service.py'), target


def test_masking_is_idempotent(gate, project: Path) -> None:
    """A placeholder is already the answer; masking it again must change nothing.

    Masking writes ``<redacted: bearer>`` into a field, and that text is itself assignment-shaped
    (`Authorization: <redacted: …>`), so a design that re-scans its own output corrupts it —
    measured earlier in this sub-task, when a second pass produced `<redacted: assignment> bearer>`.
    The rule that fired is the one that stays, and the placeholder survives whole.
    """
    value = "abcdefghijklmnop"
    command = f'printf %s "Authorization: Bearer {value}" >> src/service.py'
    gate.decide(ledger=project, tool="shell", args={"command": command})
    target = _denials(project)[0]["target"]
    assert value not in target, target
    assert target == 'printf %s "Authorization: <redacted: bearer>" >> src/service.py', target
    # Masking its own output must be a no-op: the placeholder is assignment-shaped, and a design
    # that re-scans it would rename the rule that fired.
    gate.decide(ledger=project, tool="shell", args={"command": target})
    assert _denials(project)[1]["target"] == target


def test_no_value_survives_masking(secrets_module) -> None:
    """The property behind every case above, checked directly: nothing of the value is left.

    A reader of the journal must not be able to recover the credential from the field — not the
    whole value, not the head of it. `mask` is asserted against `scan` over its own output instead
    of against a hand-written expectation, because the expectation is where the mistake usually
    is: the same rule that missed the value would also miss it in a fixture.
    """
    text = 'mysql -e "password=hunter2hunter2" db\nprintf %s "Bearer abcdefghijklmnop"\n'
    masked = secrets_module.mask(text)
    assert "hunter2hunter2" not in masked, masked
    assert "abcdefghijklmnop" not in masked, masked
    assert not secrets_module.scan(masked), f"masking left a finding: {masked!r}"


def test_a_masked_line_still_says_which_rule_fired(secrets_module) -> None:
    """A field that hides the value must still name the rule, or the reader cannot act on it."""
    masked = secrets_module.mask('curl -H "Authorization: Bearer abcdefghijklmnop" example')
    assert masked == 'curl -H "Authorization: <redacted: bearer>" example', masked


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ('printf %s "Authorization: Bearer abcdefghijklmnop" >> src/service.py', "bearer"),
        ("printf %s ghp_" + "a" * 30 + " >> src/service.py", "github"),
        ("printf %s sk-" + "a" * 20 + " >> src/service.py", "openai"),
    ],
)
def test_each_secret_shape_is_masked_in_place(
    gate, project: Path, command: str, rule: str
) -> None:
    """Three shapes, three rules, one behaviour: the value goes, the call stays.

    A single case would prove masking happens; it would not prove it happens *to the match*
    rather than to the whole field for one rule and correctly for another.
    """
    gate.decide(ledger=project, tool="shell", args={"command": command})
    target = _denials(project)[0]["target"]
    assert f"<redacted: {rule}>" in target, target
    assert target.startswith("printf %s"), target
    assert target.endswith(">> src/service.py"), target


# --- phase and rule ----------------------------------------------------------


def test_a_denial_records_the_phase_it_happened_in(gate, project: Path) -> None:
    """Phase 1 refuses an edit; the same edit is allowed in phase 4. The record must say which."""
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    record = _denials(project)[0]
    assert record.get("phase") == 1


def test_phase_four_and_phase_one_records_differ(gate, project: Path) -> None:
    """Two refusals under different phases must not look identical.

    In phase 4 the refusal is about the missing `Working on:` marker, not about the phase —
    so both `phase` and `verdict_of` differ between the two records.
    """
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    helpers.set_phase(project, 4)
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    records = _denials(project)
    assert len(records) == 2
    assert records[0]["phase"] == 1
    assert records[1]["phase"] == 4
    assert records[0]["verdict_of"] != records[1]["verdict_of"]


def test_verdict_of_names_the_rule_not_the_wording(gate, project: Path) -> None:
    """Two rules whose text starts identically must still be distinguishable.

    The class was recovered by parsing `why`, which breaks silently on any reword. A stable
    identifier is the point of the field.
    """
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    phase_refusal = _denials(project)[0]
    gate.decide(ledger=project, tool="write", args={"file_path": "/outside-a/x.py"})
    outside_refusal = _denials(project)[1]
    assert phase_refusal["verdict_of"] == "phase-ban"
    assert outside_refusal["verdict_of"] == "outside-project"


def test_the_ledger_a_refusal_belongs_to_is_named(gate, project: Path) -> None:
    """F9: a record used to land wherever mtime pointed. The record must say whose it is."""
    gate.decide(ledger=project, tool="write", args={"file_path": "src/service.py"})
    record = _denials(project)[0]
    assert record.get("ledger") == str(project.resolve())


# --- compatibility -----------------------------------------------------------


def test_a_record_without_the_new_fields_still_reads(journal, project: Path) -> None:
    """Old journals stay readable: the new fields are not in `REQUIRED_FIELDS`.

    There are 133 such records on disk in this repository. A migration requirement would
    mean every existing journal has to be rewritten before the gate works again.
    """
    assert journal.REQUIRED_FIELDS == ("at", "event", "why")
    helpers.append(project, event="deny", tool="shell", why="phase 1 forbids this")
    events = journal.read_events(project)
    assert len(events) == 1
    assert journal.state_of(project)["phase"] == 1


def test_an_old_record_with_the_truncation_flag_still_reads(journal, project: Path) -> None:
    """The removed field must be readable where it already sits, not only absent from new ones.

    One record with ``truncated: true`` and a cut target exists on disk from TASK-087. Dropping
    the field from the writer says nothing about a reader, so the reader is tested against the
    old shape rather than assumed to cope with it.
    """
    helpers.append(
        project,
        event="deny",
        tool="shell",
        why="phase 1 forbids this",
        target="cat > src/service.py <<'EOF'\\n",
        truncated=True,
    )
    events = journal.read_events(project)
    assert len(events) == 1
    assert events[0]["truncated"] is True
    assert journal.state_of(project)["phase"] == 1


def test_an_old_record_whose_target_is_only_a_redaction_still_reads(
    journal, project: Path
) -> None:
    """Records written before ST-12 hold ``<redacted: assignment>`` and nothing else.

    They are on disk in this repository's own journals (2026-11-28T11:57:38Z). A reader is
    tested against that shape because the writer stopping is not the same as the file becoming
    readable: ``target`` is not a required field, and the value must load as the string it is.
    """
    helpers.append(
        project,
        event="deny",
        tool="shell",
        why="Phase 1 forbids production-code changes",
        target="<redacted: assignment>",
        phase=1,
        verdict_of="phase-ban",
    )
    events = journal.read_events(project)
    assert len(events) == 1
    assert events[0]["target"] == "<redacted: assignment>"
    assert journal.state_of(project)["phase"] == 1
