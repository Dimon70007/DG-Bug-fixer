"""The project rule: where the agent learns it must check the contract, and how it is merged.

Spec §18.3 makes a rules file the **main** initialisation channel. The gate works — it denies the
first unauthorised write — but it denies late: by then the agent has read code and planned against
a test command that may not exist. A rules file is read by the harness itself, with no hook and no
delivery problem, so the same instruction arrives before the first edit.

What the block says is deliberately short and static: *run `bugfixer doctor`, and a non-zero exit
means initialise first*. It names no command, no path and no ledger, because those are the
project's answers and live in the contract: a copy here would be a second place to keep in step,
and the stale copy is the one the agent would trust.

**Merged, never overwritten.** `AGENTS.md` and `CLAUDE.md` almost always exist and belong to the
project. The block carries a marker; a re-install replaces only what is between the markers, and
`--uninstall` removes exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from . import perms

#: Opening marker. The parenthetical is for a human reading `AGENTS.md`, not for the parser.
BEGIN: Final[str] = "<!-- bug-fixer:project-rule:begin — managed by the bug-fixer plugin, merged not overwritten -->"

#: Closing marker. Both markers must be present for the file to count as carrying the rule: half a
#: block is a corrupt file, and appending a second copy would make it worse.
END: Final[str] = "<!-- bug-fixer:project-rule:end -->"

#: The instruction itself. Wording is the draft recorded in spec §18.3, kept verbatim so the rule
#: and the norm cannot drift; the same text goes into `.mdc`, `AGENTS.md` and `CLAUDE.md`, because a
#: per-harness wording would be a per-harness judgement about the project's state.
BODY: Final[tuple[str, ...]] = (
    "## bug-fixer",
    "",
    "Проект использует bug-fixer. Перед первой правкой кода запусти `bugfixer doctor` из корня",
    "проекта. Код возврата не `0` — сначала инициализация по `references/init.md`, потом работа.",
    "Пока контракт не подтверждён, гейт откажет в правках: это ожидаемый отказ, а не сбой.",
)

#: Cursor's rule file, relative to the project root. `alwaysApply` is what makes it reach every
#: session without the operator selecting it.
CURSOR_RULE_FILE: Final[Path] = Path(".cursor") / "rules" / "bug-fixer.mdc"

#: Cursor frontmatter. `alwaysApply` is the registration: without it the file is an unused rule
#: that looks installed.
CURSOR_FRONTMATTER: Final[tuple[str, ...]] = (
    "---",
    "description: bug-fixer — check the contract before editing code",
    "alwaysApply: true",
    "---",
)

#: The file every harness but Cursor reads, and the one Claude Code imports.
AGENTS_FILE: Final[Path] = Path("AGENTS.md")

#: Claude Code's own file: it reads `CLAUDE.md`, and `@AGENTS.md` pulls the shared block in
#: without keeping a second copy of the text.
CLAUDE_FILE: Final[Path] = Path("CLAUDE.md")

#: The one-line import Claude Code understands. Marked like the block so removal is exact.
CLAUDE_IMPORT: Final[str] = "@AGENTS.md"

#: Harness ids whose rule placement this module knows. `install.py` derives its targets from this,
#: so a harness added to the installer without a rule fails here rather than silently writing none.
HARNESSES: Final[tuple[str, ...]] = ("cursor", "claude", "codex", "opencode", "openclaw")


@dataclass(frozen=True)
class Merged:
    """What a merge did to one file.

    Attributes:
        path: File that was (or would be) written.
        action: ``written``, ``updated``, ``unchanged``, ``added-import`` or ``kept``.
    """

    path: Path
    action: str


def _clamp_created(path: Path) -> None:
    """Clamp the mode of a file this module just created.

    Args:
        path: File just created.
    """
    path.chmod(perms.FILE_MODE)


def _clamp_dirs(path: Path) -> None:
    """Create a parent directory chain and clamp every level this module created.

    Args:
        path: Directory to ensure.
    """
    perms.ensure_dir(path)


def block() -> str:
    """The marked block, exactly as it is written into a project file.

    Returns:
        Markers around the instruction, with a trailing newline.
    """
    return "\n".join([BEGIN, *BODY, END, ""])


def cursor_rule_text() -> str:
    """The full contents of Cursor's rule file.

    Returns:
        Frontmatter followed by the same marked block every other harness gets. The block is kept
        marked even though this file is entirely ours: the marker is what `--uninstall` looks for,
        and a rule that is removed by a filename guess breaks the day the file is renamed.
    """
    return "\n".join([*CURSOR_FRONTMATTER, "", block()])


def write_owned(path: Path, contents: str, *, dry_run: bool = False) -> Merged:
    """Write a file this plugin owns outright, leaving a project-owned file alone.

    Cursor's rule file is ours: nothing else writes `.cursor/rules/bug-fixer.mdc`. So it is written
    whole and deleted on removal, unlike `AGENTS.md`, which belongs to the project and is merged
    into.

    Args:
        path: Destination file.
        contents: Full file contents.
        dry_run: When true, report without writing.

    Returns:
        What happened, or would happen.
    """
    if path.is_file() and path.read_text(encoding="utf-8") == contents:
        return Merged(path=path, action="unchanged")
    action = "updated" if path.is_file() else "written"
    if not dry_run:
        _clamp_dirs(path.parent)
        path.write_text(contents, encoding="utf-8")
        _clamp_created(path)
    return Merged(path=path, action=action)


def remove_owned(path: Path, *, dry_run: bool = False) -> Merged:
    """Delete a file this plugin owns.

    Args:
        path: File to delete.
        dry_run: When true, report without deleting.

    Returns:
        What happened, or would happen.
    """
    if not path.is_file():
        return Merged(path=path, action="absent")
    if not dry_run:
        path.unlink()
    return Merged(path=path, action="removed")


def for_harness(project: Path, harness: str, *, dry_run: bool = False) -> list[Merged]:
    """Place the rule for one harness.

    The mapping from harness to files lives here and only here: `install.py` (one harness per
    install) and `init` (every harness at once) both call it, so the two routes cannot drift into
    writing different files for the same harness.

    Args:
        project: Project root.
        harness: Target id as the installer spells it (``cursor``, ``claude``, ``codex``,
            ``opencode``, ``openclaw``).
        dry_run: When true, report without writing.

    Returns:
        What happened to each file.
    """
    marked = block()
    if harness == "cursor":
        # Ours alone: written whole, removed whole.
        return [write_owned(project / CURSOR_RULE_FILE, cursor_rule_text(), dry_run=dry_run)]
    if harness == "claude":
        # Claude Code reads `CLAUDE.md`, and `@AGENTS.md` is its import — so the text has one home
        # and the import points at it, instead of a second copy that could go stale.
        return [
            merge(project / AGENTS_FILE, marked, dry_run=dry_run),
            ensure_claude_import(project / CLAUDE_FILE, dry_run=dry_run),
        ]
    # Codex, OpenCode and OpenClaw read `AGENTS.md` from the repository root.
    return [merge(project / AGENTS_FILE, marked, dry_run=dry_run)]


def install(project: Path, *, dry_run: bool = False) -> list[Merged]:
    """Place the rule for every harness, for an entry point that does not know its own harness.

    Called by `bugfixer init`: a plugin installed from a marketplace never runs `install.py`, and
    without this the main initialisation channel (§18.3) would not exist at all in a plugin-only
    install — the project would get a gate and no instruction to run the check first.

    Writing all three is not redundancy: each harness reads its own file and ignores the others.

    Idempotent by construction: the Cursor file is ours and written whole, the `AGENTS.md` block is
    merged by marker, and the Claude import is added once.

    Args:
        project: Project root.
        dry_run: When true, report without writing.

    Returns:
        What happened to each file.
    """
    placed: list[Merged] = []
    seen: set[Path] = set()
    for harness in HARNESSES:
        for merged in for_harness(project, harness, dry_run=dry_run):
            # `AGENTS.md` is written by three harnesses; report it once, from the first of them.
            if merged.path in seen:
                continue
            seen.add(merged.path)
            placed.append(merged)
    return placed


def merge(path: Path, contents: str, *, dry_run: bool = False) -> Merged:
    """Write or update a file so it carries ``contents`` and keeps everything else.

    Three cases, and the difference matters:

    * file absent → write it whole;
    * markers present → replace only what is between them, so the project's own text survives;
    * markers absent → append the block, because the file is the project's and its order is its
      own.

    Args:
        path: Destination file.
        contents: Marked block to place.
        dry_run: When true, report without writing.

    Returns:
        What happened, or would happen.
    """
    if not path.is_file():
        if not dry_run:
            _clamp_dirs(path.parent)
            path.write_text(contents, encoding="utf-8")
            _clamp_created(path)
        return Merged(path=path, action="written")

    existing = path.read_text(encoding="utf-8")
    updated = _replace_or_append(existing, contents)
    if updated == existing:
        return Merged(path=path, action="unchanged")
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    return Merged(path=path, action="updated")


def _replace_or_append(existing: str, contents: str) -> str:
    """Put ``contents`` into an existing file without disturbing the rest.

    Args:
        existing: Current file contents.
        contents: Marked block.

    Returns:
        The new contents; identical to ``existing`` when the block is already correct.
    """
    start = existing.find(BEGIN)
    end = existing.find(END)
    if start != -1 and end > start:
        replaced = existing[:start] + contents + existing[end + len(END) + 1 :]
        return replaced
    separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + separator + contents


def carries_rule(text: str) -> bool:
    """Whether a file already carries a complete block.

    Args:
        text: File contents.

    Returns:
        ``True`` when both markers are present and ordered.
    """
    start = text.find(BEGIN)
    end = text.find(END)
    return start != -1 and end > start


def strip(path: Path, *, dry_run: bool = False) -> Merged:
    """Remove this plugin's block from a file, keeping everything else.

    A file that held only our block is deleted: leaving an empty `AGENTS.md` behind is a file the
    project did not have, and its presence would look like a project decision.

    Args:
        path: File to clean.
        dry_run: When true, report without writing.

    Returns:
        What happened, or would happen.
    """
    if not path.is_file():
        return Merged(path=path, action="absent")
    existing = path.read_text(encoding="utf-8")
    start = existing.find(BEGIN)
    end = existing.find(END)
    if start == -1 or end <= start:
        return Merged(path=path, action="kept")

    remaining = (existing[:start].rstrip("\n") + "\n" + existing[end + len(END) + 1 :].lstrip("\n"))
    if not dry_run:
        if remaining.strip():
            path.write_text(remaining, encoding="utf-8")
        else:
            path.unlink()
    return Merged(path=path, action="updated")


def ensure_claude_import(path: Path, *, dry_run: bool = False) -> Merged:
    """Make `CLAUDE.md` import `AGENTS.md`, once.

    Claude Code reads `CLAUDE.md`, not `AGENTS.md`, and `@AGENTS.md` is its documented import. A
    project that already imports it by hand is left alone: a second identical import is noise the
    operator did not ask for.

    Args:
        path: `CLAUDE.md` path.
        dry_run: When true, report without writing.

    Returns:
        What happened, or would happen.
    """
    if path.is_file() and CLAUDE_IMPORT in path.read_text(encoding="utf-8"):
        return Merged(path=path, action="kept")
    if not path.is_file():
        contents = f"{BEGIN}\n{CLAUDE_IMPORT}\n{END}\n"
        if not dry_run:
            _clamp_dirs(path.parent)
            path.write_text(contents, encoding="utf-8")
            _clamp_created(path)
        return Merged(path=path, action="written")
    existing = path.read_text(encoding="utf-8")
    addition = f"{BEGIN}\n{CLAUDE_IMPORT}\n{END}\n"
    separator = "" if existing.endswith("\n") else "\n"
    if not dry_run:
        path.write_text(existing + separator + addition, encoding="utf-8")
    return Merged(path=path, action="added-import")
