"""Tests for the bug-fixer skill (TASK-078) and its installer.

Two layers:

* **Content** - the skill tells the agent the right thing: FSM with a way back,
  a hard iteration limit, separate N/M counters, a four-part success criterion,
  and this repo's ledger/search/test conventions.
* **Installer** - copies land where they should, dry-run writes nothing, bad
  input fails closed, and permissions never grant world access.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

import bugfixer_helpers as jrnl

_SKILL_DIR = jrnl.SKILL_DIR
_HOOKS = jrnl.HOOKS
_SKILL_MD = _SKILL_DIR / "SKILL.md"
_README = _SKILL_DIR / "README.md"
_WORKFLOW = _SKILL_DIR / "references" / "workflow.md"
_CONTRACT_TEMPLATE = _SKILL_DIR / "references" / "project-contract.md"
_TEMPLATE = _SKILL_DIR / "templates" / "finding-block.md"
_INSTALL_PY = _SKILL_DIR / "install.py"
_INSTALL_SH = _SKILL_DIR / "install.sh"

_REQUIRED_SECTIONS = (
    "Железное правило",
    "Конечный автомат",
    "Формат записей — обязательный",
    "Алгоритм по фазам",
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "Phase 4",
    "Phase 5",
    "Формат гипотезы `### H<n>`",
    "Активная гипотеза",
    "Метрики",
    "Секреты в логах",
    "Структура файла задачи",
    "Частичный фикс",
    "Неверный baseline",
    "Отчёт оператору",
    "Запреты",
)

#: Every edge name from the spec. A skill that names seven of eight teaches an
#: automaton that cannot reach one of its exits.
_EDGE_NAMES = (
    "REPRODUCED",
    "ALREADY_TRIED",
    "FIRST_GUESS",
    "HYPOTHESIS_READY",
    "PROVEN",
    "NEXT_IN_QUEUE",
    "QUEUE_EXHAUSTED",
    "DEEPEN_SAME",
)


def _load_contract():
    """Import the contract schema module from its file path."""
    path = _SKILL_DIR / "lib" / "bugfixer" / "contract.py"
    spec = importlib.util.spec_from_file_location("bugfixer_contract_skill_test", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_installer():
    """Import the installer module from its file path.

    The module is registered in ``sys.modules`` before execution because it
    declares a dataclass with string annotations, which ``dataclasses`` resolves
    through the module registry.
    """
    spec = importlib.util.spec_from_file_location("install_ai_skill_test", _INSTALL_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _skill_text() -> str:
    """Return the SKILL.md body."""
    return _SKILL_MD.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    """Parse the flat ``key: value`` pairs of the YAML frontmatter block.

    Only the keys this skill uses are parsed; the description may be a folded
    scalar, so continuation lines are joined.
    """
    if not text.startswith("---\n"):
        raise AssertionError("SKILL.md must start with a --- frontmatter block")
    end = text.find("\n---\n", 4)
    assert end != -1, "frontmatter block is not closed"
    block = text[4:end]

    parsed: dict[str, str] = {}
    key: str | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line[0] not in " \t" and ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            parsed[key] = value.strip()
        elif key and line.strip() not in {"", ">"}:
            parsed[key] = f"{parsed[key]} {line.strip()}".strip()
    return parsed


# --- content -----------------------------------------------------------------


def test_skill_directory_is_complete() -> None:
    """SKILL.md plus every referenced support file must exist."""
    for path in (_SKILL_MD, _README, _CONTRACT_TEMPLATE, _WORKFLOW, _TEMPLATE):
        assert path.is_file(), f"missing skill artefact: {path}"


def test_frontmatter_is_valid() -> None:
    """Frontmatter carries the name, a trigger description, and a version."""
    meta = _frontmatter(_skill_text())
    assert meta["name"] == "bug-fixer"
    assert len(meta.get("description", "")) > 40, "description must explain when to use the skill"
    assert '"3.0.0"' in meta.get("metadata", ""), "metadata.version is not 3.0.0"
    assert "пятифазн" in meta["description"], (
        "the description still advertises the three-phase cycle, so the skill would be "
        "chosen or skipped by a wrong trigger"
    )


def test_required_sections_present() -> None:
    """Every phase and every guard section is present."""
    text = _skill_text()
    missing = [section for section in _REQUIRED_SECTIONS if section not in text]
    assert not missing, f"SKILL.md is missing sections: {missing}"


def test_no_three_phase_scheme_survives() -> None:
    """Invariant 16: two automata in one file means the agent reads the wrong one.

    ``BACK_TO_PHASE_1`` was an edge in 2.1 and is not one in 3.0; leaving it in the
    text teaches a transition the gate rejects.
    """
    text = _skill_text()
    for stale in ("BACK_TO_PHASE_1", "трёхфазн", "Phase 3 — DECIDE", "M (эксперименты)"):
        assert stale not in text, f"SKILL.md still carries the 2.1 scheme: {stale!r}"
    assert "`1->2`, `2->1`, `2->3`, `3->1`, `3->2`" not in text


def test_all_eight_edges_are_named() -> None:
    """Invariant 1 from the agent's side: a missing name is an unreachable exit."""
    text = _skill_text()
    missing = [edge for edge in _EDGE_NAMES if edge not in text]
    assert not missing, f"SKILL.md does not name edge(s): {missing}"


def test_both_4_to_3_edges_are_distinguished() -> None:
    """The defect the split exists for: one edge for two meanings created a dead end."""
    text = _skill_text()
    assert "QUEUE_EXHAUSTED" in text and "DEEPEN_SAME" in text
    section = text[text.index("## Конечный автомат"):text.index("## Счётчики и лимиты")]
    assert "не дубликат" in section, "the two 4→3 edges must be explained, not just listed"
    assert "тупиков нет" in section.lower(), "the verdict × queue matrix must be shown"


def test_counters_are_a_and_rechecks() -> None:
    """``A`` counts probes that reached a code change; ``R<h>`` repairs the test."""
    text = _skill_text()
    assert "`A`" in text and "`R<h>`" in text
    assert re.search(r"\|\s*`A`[^|]*\|[^|]*\|\s*\*\*10\*\*\s*\|", text), (
        "A is not capped at 10 in the counter table"
    )
    assert re.search(r"\|\s*`R<h>`[^|]*\|[^|]*\|\s*\*\*5\*\*\s*\|", text), (
        "R<h> is not capped at 5 in the counter table"
    )
    assert "не увеличивает `A`" in text, "Recheck must be stated as free of A"
    assert "by operator" in text, "the operator-only reset must be documented"
    assert "LIMIT_REACHED" in text


def test_every_record_marker_has_a_format_row() -> None:
    """The agent writes what it reads here; a missing marker is a transition that never opens."""
    text = _skill_text()
    section = text[
        text.index("## Формат записей — обязательный"):text.index("## Алгоритм по фазам")
    ]
    required = (
        "FAILING TEST:",
        "BASELINE:",
        "Q:",
        "Clarification gap:",
        "Assumption:",
        "### H<n>",
        "### F<n>",
        "Kind",
        "#### Records",
        "Attempt <k> consumed",
        "Recheck H<n> #<k>",
        "Working on: H<n>",
        "ФАКТ (RED):",
        "ФАКТ (GREEN):",
        "ФАКТ (логи):",
        "Bug: stable",
        "ФАКТ (дельта):",
        "ФАКТ (регрессия):",
        "Revert:",
        "Secrets scan:",
        "Corner cases: none",
        "Merge:",
        "NEW INFORMATION:",
        "Resolution:",
        "STATUS:",
        "## Remaining",
        "## Report",
        "Counter reset:",
    )
    missing = [marker for marker in required if marker not in section]
    assert not missing, f"record-format table omits: {missing}"


def test_table_form_is_named_as_wrong() -> None:
    """Invariant 17b from the agent's side: the table form is invisible to the gate."""
    text = _skill_text()
    section = text[
        text.index("## Формат записей — обязательный"):text.index("## Алгоритм по фазам")
    ]
    assert "Табличная форма — ошибка" in section
    assert "| Attempt | 3 consumed |" in section, "the rejected example must be shown literally"


def test_working_on_is_one_pointer() -> None:
    """Finding D-8: a marker per switch reads as a log, and two lines are refused."""
    text = _skill_text()
    section = text[text.index("## Активная гипотеза"):text.index("## Метрики")]
    assert "Одна на файл" in section
    assert "переписывай" in section.lower()
    assert re.search(r"[Дд]ве строки", section), "the two-marker refusal must be stated"


def test_success_criterion_is_four_parts() -> None:
    """Phase 5 success lists the RED test, e2e, no regression, and clean logging."""
    text = _skill_text()
    success = text[text.index("## Критерий `DONE`"):text.index("## Отчёт оператору")]
    assert "RED-набор" in success
    assert "e2e" in success
    assert "baseline" in success
    assert "логи" in success


def test_ledger_forbids_a_second_file() -> None:
    """The skill must put findings in the task file and forbid DEBUG.md."""
    text = _skill_text()
    assert "DEBUG.md" in text
    assert "не создаётся" in text or "Не создавать" in text
    assert "F<n>" in text


def test_no_forbidden_research_or_privilege_instructions() -> None:
    """The skill must not tell the agent to curl the web or run sudo.

    Search tooling is deliberately NOT named in SKILL.md any more: the skill is
    project-agnostic, so the allowed tools come from the project contract. What
    must hold is that the contract is the named source, and that no direct
    network fetch is ever suggested.
    """
    text = _skill_text()
    assert "контракт" in text.lower(), (
        "SKILL.md must point at the project contract for search tooling"
    )
    for forbidden in ("curl https://", "wget https://"):
        assert forbidden not in text
    # The only mentions of sudo-like tools must be prohibitions.
    for privileged in ("sudo", "pkexec"):
        for match in re.finditer(rf".{{0,80}}{privileged}.{{0,80}}", text):
            assert "Не запускать" in match.group(0) or "печатай" in match.group(0).lower(), (
                f"privileged tool {privileged} appears outside a prohibition: {match.group(0)!r}"
            )


def test_contract_template_covers_every_required_field() -> None:
    """The template and the validator must require the same set — checked through the schema.

    The previous version looked for the words "Ledger", "Tests", "Search", "prohibitions".
    It passed while the template had no `Test paths:` at all, which is exactly the defect that
    left the gate unable to find its ledger. Comparing against the schema's field list makes
    that class of drift impossible: a field the validator requires is a field the template
    must teach.
    """
    contract = _load_contract()
    template = (_SKILL_DIR / "references" / "project-contract.md").read_text(encoding="utf-8")
    missing = [
        name
        for name, rule in contract.FIELD_POINTS.items()
        if rule.required and rule.names.search(template) is None
    ]
    assert not missing, f"the template never teaches required fields: {missing}"
    assert "bug-fixer.project.md" in template, "template does not name the lookup file"
    assert len(contract.SPEC_POINTS) == 4, "the contract must require exactly the four §6 points"


def test_skill_is_not_pinned_to_one_repository() -> None:
    """SKILL.md must stay portable: no repo-specific commands or paths."""
    text = _skill_text()
    for marker in ("make test", "ctest", "atomic-llama", ".cursorrules", "tasks/TASK"):
        assert marker not in text, (
            f"SKILL.md hard-codes project-specific {marker!r}; it belongs in the contract"
        )


def test_referenced_paths_resolve() -> None:
    """Every file the skill points at must exist relative to the skill directory."""
    text = _skill_text()
    referenced = set(re.findall(r"`((?:references|templates)/[^`]+)`", text))
    assert referenced, "SKILL.md does not reference its support files"
    missing = sorted(rel for rel in referenced if not (_SKILL_DIR / rel).is_file())
    assert not missing, f"SKILL.md references missing files: {missing}"

def test_the_plugin_tree_carries_no_host_project_contract() -> None:
    """A plugin installed into another project must not ship this repo's answers.

    The filled contract used to live here, naming ``make test``, ``ctest -L main`` and one
    project's own rules. In someone else's repository those values are wrong, and the skill
    would read them as authoritative. Only the template may travel.
    """
    shipped = [
        path for path in _SKILL_DIR.rglob("*")
        if path.is_file() and "tests" not in path.relative_to(_SKILL_DIR).parts
    ]
    assert shipped, "the plugin tree is empty; the check would pass against nothing"
    stray = [p.relative_to(_SKILL_DIR).as_posix() for p in shipped if p.name == "repo-contract.md"]
    assert not stray, f"a filled host contract ships inside the plugin: {stray}"
    for marker in ("ctest -L main", "cluster_models"):
        hits = [
            path.relative_to(_SKILL_DIR).as_posix()
            for path in shipped
            if marker in path.read_text(encoding="utf-8", errors="replace")
        ]
        assert not hits, f"{marker!r} (this host's tooling) appears in {hits}"
    assert "DEBUG.md" in _CONTRACT_TEMPLATE.read_text(encoding="utf-8")


def test_template_defines_the_finding_block() -> None:
    """The template carries the mandatory block fields of §5.2, not 2.1's prose fields."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    for field in ("Kind", "Scope", "Responds to", "Supersedes", "Iteration", "Outcome"):
        assert field in text, f"template is missing {field!r}"
    for marker in ("ФАКТ (RED):", "ФАКТ (GREEN):", "ФАКТ (логи):", "Bug: stable",
                   "Attempt <k> consumed", "Secrets scan:", "Revert:"):
        assert marker in text, f"template never shows {marker!r}"
    assert "route / research / probe" in text, "Kind's three values must be named"
    # 2.1 fields that no longer exist in the record format.
    for stale in ("Experiment", "**Consequence.**"):
        assert stale not in text, f"template still carries the 2.1 field {stale!r}"


def test_hypothesis_template_defines_the_subtask_block() -> None:
    """ST-6: one format for ``H0`` and ``H1…Hn``, with the derived status explained."""
    text = (_SKILL_DIR / "templates" / "hypothesis-block.md").read_text(encoding="utf-8")
    for field in ("Rank", "Status", "Proposed", "Origin", "Mechanism", "Prediction", "Discriminator"):
        assert field in text, f"hypothesis template is missing {field!r}"
    assert "#### Records" in text
    assert "derived" in text, "the Status field must be described as derived, not authored"
    assert "PARTIALLY" in text, "the partial-hit rule must be stated: it stays in the queue"


# --- per-harness adaptation (TASK-079.5 ST-2) --------------------------------


_HARNESSES = ("Cursor", "OpenClaw", "Claude Code", "Codex", "OpenCode")


def _code_lines(text: str) -> str:
    """Strip block and line comments so a check inspects code, not prose."""
    without_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(
        line for line in without_block.splitlines() if not line.strip().startswith("//")
    )


def test_no_session_start_hook_is_shipped() -> None:
    """TASK-079.5 ST-3 declined session-start injection; this keeps it declined.

    Every harness already injects the skill's name and description at session start,
    which is the mechanism description matching runs on. A sessionStart hook would
    inject a second copy of that trigger text on five wirings, and it cannot be built
    on all five anyway (OpenCode has no context-injection session hook, OpenClaw's
    session_start is observe-only, Cursor's is absent in cloud agents).

    If someone wants to add it, this test is where they have to argue the
    duplication point rather than cite superpowers.
    """
    adapters = [
        _HOOKS / "cursor.hooks.json",
        _HOOKS / "claude.settings.json",
        _HOOKS / "codex.hooks.json",
    ]
    for adapter in adapters:
        text = adapter.read_text(encoding="utf-8")
        for event in ("sessionStart", "SessionStart"):
            assert event not in text, (
                f"{adapter.name} wires {event}, but ST-3 declined session-start injection: "
                "the harness already injects name+description"
            )
    for adapter in (_HOOKS / "opencode-plugin.ts", _HOOKS / "openclaw-plugin.ts"):
        code = _code_lines(adapter.read_text(encoding="utf-8"))
        assert "session_start" not in code and "sessionStart" not in code, (
            f"{adapter.name} wires a session hook, but ST-3 declined it"
        )


def test_decline_is_recorded_where_a_future_reader_will_find_it() -> None:
    """A declined decision that is not written down gets re-proposed every few months.

    The decision itself lives in the plugin, not in a host project's task file: a reader of
    a clone has no ``tasks/`` directory, so the reason has to be next to the code it governs.
    """
    adapters = list(_HOOKS.glob("*.ts")) + list(_HOOKS.glob("*.json"))
    assert adapters, "no adapter files found; the check would pass against nothing"
    recorded = [
        path for path in adapters
        if "DECLINED" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert recorded, (
        f"no adapter records the DECLINED session-start decision: {[p.name for p in adapters]}"
    )
    for path in recorded:
        text = path.read_text(encoding="utf-8", errors="replace")
        section = text[text.index("DECLINED"):][:800]
        # The reason must be the duplication argument, not "we lacked time".
        assert "second copy" in section or "duplicate" in section
        assert "name and description" in section

    """The behaviour rule that the gate's F5 defect taught: unknown is not "no".

    A skill that skips what it does not understand fails invisibly, and an invisible
    failure is never reported. The rule must be in the skill body, not only in the
    gate's implementation.
    """
    text = _skill_text()
    assert "## Никогда не пропускать молча" in text
    rule = text[text.index("## Никогда не пропускать молча"):text.index("## Вызов скилла")]
    assert "упасть" in rule or "deny" in rule
    assert "неотличим" in rule, "the rule must explain why silence is the problem"
    # The deliberate exception has to be named, or the rule reads as "deny everything".
    assert "инертность" in rule
    # And the testing half: a test that cannot fail proves nothing.
    assert "предыдущей" in rule


def test_skill_has_no_silent_fallbacks_in_its_own_instructions() -> None:
    """The skill must not tell the agent to guess when a value is missing."""
    text = _skill_text()
    body = text[text.index("## Phase 0"):]
    for hedge in ("если не найдёшь — используй", "по умолчанию возьми", "если не указано, бери"):
        assert hedge not in body, f"SKILL.md suggests guessing: {hedge!r}"


def test_invocation_table_covers_every_harness() -> None:
    """An operator reading only SKILL.md must learn how to invoke it where they are."""
    text = _skill_text()
    section = text[text.index("## Вызов скилла в разных средах"):text.index("## Конечный автомат")]
    missing = [harness for harness in _HARNESSES if harness not in section]
    assert not missing, f"invocation table omits harness(es): {missing}"


def test_invocation_syntax_matches_the_matrix() -> None:
    """Each harness's documented syntax is named, not a shared guess.

    Cursor and Claude use `/name`, OpenClaw and Codex use `$name`, OpenCode loads
    the skill through a tool. Copying one harness's syntax into another is exactly
    the mistake this table exists to prevent.
    """
    section = _skill_text()[
        _skill_text().index("## Вызов скилла в разных средах"):_skill_text().index("## Конечный автомат")
    ]
    rows = {
        line.split("|")[1].strip(): line
        for line in section.splitlines()
        if line.startswith("| ") and line.count("|") >= 3
    }
    # Cursor: slash-prefix command palette, then the skill name from the list.
    assert "`/`" in rows["Cursor"]
    assert "`bug-fixer`" in rows["Cursor"]
    # Claude Code: the skill name *is* the command.
    assert "/bug-fixer" in rows["Claude Code"]
    # OpenClaw and Codex use the dollar-prefix mention form, not a slash command.
    assert "$bug_fixer" in rows["OpenClaw"]
    assert "/skills" in rows["Codex"]
    assert "$" in rows["Codex"]
    # OpenCode has no command form documented; the model loads it through a tool.
    assert "`skill`" in rows["OpenCode"]


def test_search_fallback_is_a_normal_mode_not_a_failure() -> None:
    """A harness with no search tool must skip step 3.3/3.4 without calling it degradation."""
    text = _skill_text()
    step = text[text.index("## Phase 3 — RESEARCH"):text.index("## Phase 4 — PROBE")]
    assert "пропустить" in step.lower() or "пропускается" in step.lower()
    assert "нормальный режим" in step
    # "Degradation" may only appear as a negation: framing a skipped step as a
    # failure pushes the agent to fake a search to look diligent.
    for match in re.finditer(r".{0,40}деградац.{0,20}", step):
        assert "не деградац" in match.group(0).lower(), (
            f"phase 3 frames a skipped search as degradation: {match.group(0)!r}"
        )


def test_no_harness_specific_tool_name_is_used_as_an_instruction() -> None:
    """Tool names may only appear in the mapping table, never inside a step.

    Phase text says "write the file" or "run the suite". A step that says `Bash`
    is a step that silently does not apply on four harnesses out of five.
    """
    text = _skill_text()
    body = text[text.index("## Конечный автомат"):]
    for tool in ("`Bash`", "`Shell`", "`apply_patch`", "`WebSearch`", "`WebFetch`"):
        assert tool not in body, (
            f"phase instructions name the harness tool {tool}; put it in the mapping table"
        )


def test_fsm_surface_is_the_eight_edge_automaton() -> None:
    """The adaptation may add vocabulary; the automaton itself is fixed by the spec.

    This replaces TASK-079.5's "phases stay put" check: 3.0 deliberately changes the
    automaton, so the guard now pins the *new* surface instead of the old one.
    """
    text = _skill_text()
    for phase in (
        "## Phase 1 — REPRODUCE",
        "## Phase 2 — HYPOTHESIZE & PROBE",
        "## Phase 3 — DECIDE",
    ):
        # 2.1 headings are gone; the phase numbering now lives in the algorithm table.
        assert phase not in text, f"stale phase heading survived: {phase}"
    for phase in ("## Phase 1 — BASELINE", "## Phase 2 — ROUTE", "## Phase 3 — RESEARCH",
                  "## Phase 4 — PROBE", "## Phase 5 — DECIDE"):
        assert phase in text, f"phase section missing: {phase}"
    for outcome in ("`DONE`", "`PARTIAL`", "`LIMIT_REACHED`", "`BLOCKED`"):
        assert outcome in text, f"outcome removed: {outcome}"
    assert "нет" in text[text.index("Из фазы 5 рёбер"):][:40].lower()


# --- installer ---------------------------------------------------------------


def _run_installer(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the skill's own shell wrapper, so the real CLI contract is exercised."""
    return subprocess.run(
        ["bash", str(_INSTALL_SH), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_installer_lives_inside_the_skill() -> None:
    """The installer must be part of the skill: a clone alone has to install itself.

    Before this, the installer lived in a parent project, so cloning the skill
    repository on its own left the operator with nothing to run.
    """
    assert _INSTALL_PY.is_file(), "install.py must live in the skill directory"
    assert _INSTALL_SH.is_file(), "install.sh must live in the skill directory"
    # The claim is that the installer works from a clone, so prove it from the clone side:
    # nothing it needs may live above the plugin directory.
    assert _INSTALL_PY.read_text(encoding="utf-8").count("skills/bug-fixer") == 0, (
        "the installer refers to a parent layout; a standalone clone has no parent"
    )


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """--dry-run reports the plan and leaves the filesystem untouched.

    Measured in a scratch project rather than in whichever repository holds the tests: the
    plugin must behave the same in a clone, where no ``.cursor/skills`` exists to compare.
    """
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = _run_installer("--target", "cursor", "--project", str(tmp_path), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would copy SKILL.md" in result.stdout
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after


def test_installer_is_not_copied_into_itself(tmp_path: Path) -> None:
    """Copying install.py into the target would create a nested duplicate installer.

    Run against an explicit scratch project: a project-scope target needs one, and a clone
    of the plugin has no repository above it to fall back on.
    """
    result = _run_installer("--target", "cursor", "--project", str(tmp_path), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would copy install.py" not in result.stdout
    assert "would copy install.sh" not in result.stdout


def test_unknown_target_fails_closed() -> None:
    """An unknown target exits non-zero and names the known ones."""
    result = _run_installer("--target", "nope")
    assert result.returncode == 2
    assert "unknown target" in result.stderr
    assert "cursor" in result.stderr


def test_openclaw_requires_explicit_dest() -> None:
    """openclaw never guesses a home directory."""
    result = _run_installer("--target", "openclaw")
    assert result.returncode == 2
    assert "--dest" in result.stderr


def test_all_is_project_scope_only(tmp_path: Path) -> None:
    """'all' covers the targets a repository carries, never $HOME or openclaw."""
    module = _load_installer()
    plans = module.build_plans(["all"], project=str(tmp_path))
    assert {plan.target for plan in plans} == {"cursor", "codex", "claude", "opencode"}
    for plan in plans:
        assert plan.target not in module._HOME_TARGETS
        assert tmp_path in plan.destination.parents


def test_openclaw_with_dest_lands_under_skills(tmp_path: Path) -> None:
    """With --dest, openclaw installs into <dest>/skills/<id>/."""
    result = _run_installer("--target", "openclaw", "--dest", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "skills" / "bug-fixer" / "SKILL.md").is_file()
    assert (tmp_path / "skills" / "bug-fixer" / "references" / "workflow.md").is_file()


def test_install_is_idempotent_and_prunes_stale(tmp_path: Path) -> None:
    """A second run refreshes files and removes ones no longer in the source."""
    dest_root = tmp_path / "ws"
    dest_root.mkdir()
    args = ("--target", "openclaw", "--dest", str(dest_root))

    first = _run_installer(*args)
    assert first.returncode == 0, first.stderr

    stale = dest_root / "skills" / "bug-fixer" / "references" / "obsolete.md"
    stale.write_text("stale\n", encoding="utf-8")

    second = _run_installer(*args)
    assert second.returncode == 0, second.stderr
    assert not stale.exists(), "stale file was not pruned"
    assert "prune references/obsolete.md" in second.stdout


def test_destination_file_conflict_fails_closed(tmp_path: Path) -> None:
    """A destination that exists as a file is refused, not overwritten."""
    dest_root = tmp_path / "ws"
    (dest_root / "skills").mkdir(parents=True)
    (dest_root / "skills" / "bug-fixer").write_text("not a directory\n", encoding="utf-8")

    result = _run_installer("--target", "openclaw", "--dest", str(dest_root))
    assert result.returncode == 2
    assert "not a directory" in result.stderr


def test_installed_permissions_grant_no_world_access(tmp_path: Path) -> None:
    """Dirs stay <= 770 and files <= 660, everywhere in the installed tree (repo rule 5.9).

    The check walks the whole destination, not just ``hooks/``: the plugin's own tree grows
    (``lib/`` arrived with the CLI), and a directory nobody remembered to clamp ships
    world-readable. That is how ``lib/`` was caught at 0755.
    """
    dest_root = tmp_path / "ws"
    dest_root.mkdir()
    result = _run_installer("--target", "openclaw", "--dest", str(dest_root))
    assert result.returncode == 0, result.stderr

    skill_dir = dest_root / "skills" / "bug-fixer"
    offenders: list[str] = []
    for path in skill_dir.rglob("*"):
        mode = path.stat().st_mode & 0o777
        if mode & 0o007:
            offenders.append(f"{path.relative_to(skill_dir)} grants world access ({oct(mode)})")
        elif path.is_dir() and mode > 0o770:
            offenders.append(f"{path.relative_to(skill_dir)} dir mode {oct(mode)} exceeds 770")
    assert not offenders, offenders


def test_the_plugin_tests_are_not_installed(tmp_path: Path) -> None:
    """The plugin's own tests live in its tree, and must not travel into a project.

    They moved in for portability (TASK-085 stage 1): a clone has to be able to test
    itself. An install is a different thing — it drops a copy of the suite, and its
    fixtures, into a directory the agent reads, where they are dead weight and a second
    copy to keep in step.
    """
    dest_root = tmp_path / "ws"
    dest_root.mkdir()
    result = _run_installer("--target", "openclaw", "--dest", str(dest_root))
    assert result.returncode == 0, result.stderr

    installed = dest_root / "skills" / "bug-fixer"
    assert (installed / "SKILL.md").is_file(), "the install is empty; the check would pass anyway"
    stray = sorted(
        path.relative_to(installed).as_posix()
        for path in installed.rglob("*")
        if path.name in {"tests", "pyproject.toml", "conftest.py"} or "fixtures" in path.parts
    )
    assert not stray, f"test scaffolding was installed into the project: {stray}"


def test_wrapper_contains_no_inline_python() -> None:
    """The shell wrapper only forwards argv (repo rule 5.4)."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    assert "python3 -c" not in text
    assert "python -c" not in text
    assert "<<" not in text
    assert _INSTALL_PY.name in text


def test_installer_has_no_python_syntax_errors() -> None:
    """The installer must compile under the repo interpreter."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(_INSTALL_PY)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_skill_id_comes_from_frontmatter_not_the_directory() -> None:
    """A clone can be named anything; the installed folder must still be the skill id."""
    module = _load_installer()
    assert module._skill_id() == "bug-fixer"


def test_vcs_metadata_is_never_copied() -> None:
    """A clone passed as the source must not ship its history into the skill."""
    module = _load_installer()
    files = module._iter_source_files(_SKILL_DIR)
    assert Path("SKILL.md") in files
    assert not [p for p in files if ".git" in p.parts]


@pytest.mark.parametrize("target", ["cursor", "codex", "claude", "opencode"])
def test_project_targets_map_to_expected_paths(target: str, tmp_path: Path) -> None:
    """Each project-scope environment resolves to its documented directory."""
    module = _load_installer()
    plans = module.build_plans([target], project=str(tmp_path))
    assert len(plans) == 1
    plan = plans[0]
    assert plan.destination == tmp_path / module._PROJECT_TARGETS[target] / "bug-fixer"
    assert plan.source == _SKILL_DIR


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("personal", Path(".cursor") / "skills"),
        ("personal-agents", Path(".agents") / "skills"),
        ("personal-claude", Path(".claude") / "skills"),
        ("personal-opencode", Path(".config") / "opencode" / "skills"),
        ("personal-openclaw", Path(".openclaw") / "skills"),
    ],
)
def test_personal_targets_go_to_home(target: str, expected: Path) -> None:
    """User-level targets resolve under $HOME, so one install covers every project."""
    module = _load_installer()
    plans = module.build_plans([target])
    assert len(plans) == 1
    assert plans[0].destination == Path.home() / expected / "bug-fixer"


def test_opencode_global_path_is_config_not_home() -> None:
    """OpenCode's global root is ~/.config/opencode/skills, not ~/.opencode/skills."""
    module = _load_installer()
    home = Path.home()
    plans = {p.target: p.destination for p in module.build_plans(list(module._HOME_TARGETS))}
    opencode = plans["personal-opencode"]
    assert opencode == home / ".config" / "opencode" / "skills" / "bug-fixer"
    assert home / ".opencode" / "skills" not in opencode.parents


def test_readme_describes_the_automaton_the_code_implements() -> None:
    """The README is the operator's first source; drift there misleads immediately.

    This is the check that 2.1 never had: the README described three phases for a
    whole release after the gate had five. Both files are read here, so the two
    cannot diverge again without a failing test.
    """
    readme = _README.read_text(encoding="utf-8")
    skill = _skill_text()

    for edge in _EDGE_NAMES:
        assert edge in readme, f"README never names the edge {edge}"
    assert "five-phase" in readme
    for stale in ("three-phase", "BACK_TO_PHASE_1", "Phase 2 (hypothesise & probe)",
                  "Phase 3 (decide)"):
        assert stale not in readme, f"README still describes the 2.1 scheme: {stale!r}"

    # The counters named in the README must be the ones the gate implements.
    gate = _load_gate_for_counters()
    assert f"**{gate.MAX_ATTEMPTS}**" in readme, "README's A cap disagrees with the gate"
    assert f"**{gate.MAX_RECHECKS}**" in readme, "README's R<h> cap disagrees with the gate"
    assert "Phase 4" in readme and "Phase 5" in readme
    assert "Phase 4" in skill


def _load_gate_for_counters():
    """Import the gate so its constants can be compared rather than restated."""
    return _load_for_path("bugfixer_gate_readme", _HOOKS / "bugfixer_gate.py")


def _load_for_path(name: str, path: Path):
    """Import a module from a path, registering it for dataclass lookup."""
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


def test_readme_documents_every_target() -> None:
    """The README must not drift from the installer's target table."""
    module = _load_installer()
    readme = _README.read_text(encoding="utf-8")
    missing = [target for target in module._ALL_TARGETS if f"`{target}`" not in readme]
    assert not missing, f"README does not document target(s): {missing}"
