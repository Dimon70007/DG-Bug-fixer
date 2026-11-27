#!/usr/bin/env python3
"""Install this skill into one or more agent environments.

The skill is self-contained: this script lives inside it, so the source is always
its own directory. There is no ``skills/<id>`` lookup and no dependency on a parent
project, which is what makes a clone of the skill repository installable on a
machine that has nothing else.

    python3 install.py --target cursor            # this project
    python3 install.py --target personal          # every project (Cursor)
    python3 install.py --target all --dry-run     # every in-repo target, write nothing
    python3 install.py --target openclaw --dest ~/workspace
    python3 install.py --target cursor --project /path/to/repo

Project-scope targets need a project. When this skill sits inside a git checkout,
that checkout is found automatically; a standalone clone has none, so pass
``--project`` or use a ``personal`` target.

Fail-closed, per the repo rules: an unknown target, a missing project for a
project-scope target, or a destination that exists as a non-directory aborts
instead of guessing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn

#: This file lives in the skill directory, so the source is unambiguous.
SKILL_DIR: Final[Path] = Path(__file__).resolve().parent

#: The contract schema is the same one `bugfixer init` uses, so an install-time scaffold and an
#: explicit one cannot diverge.
if str(SKILL_DIR / "lib") not in sys.path:
    sys.path.insert(0, str(SKILL_DIR / "lib"))

from bugfixer import contract as contract_module  # noqa: E402
from bugfixer import rules as rules_module  # noqa: E402

#: The contract file name, taken from the schema so there is one literal in the plugin.
CONTRACT_NAME: Final[str] = contract_module.CONTRACT_NAME


def _skill_id() -> str:
    """Read the skill id from its own ``SKILL.md`` frontmatter.

    Taken from the file rather than the directory name: a clone can be named
    anything (``~/src/solo``, ``/tmp/x``), and installing under the directory name
    would put the skill in the wrong folder with the wrong id.

    Returns:
        The ``name:`` value from the frontmatter.

    Raises:
        SystemExit: If ``SKILL.md`` is missing or has no name.
    """
    entry = SKILL_DIR / "SKILL.md"
    if not entry.is_file():
        _fail(f"{SKILL_DIR} has no SKILL.md; this script must live inside a skill")
    match = re.search(r"^name:[ \t]*(\S+)[ \t]*$", entry.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        _fail(f"{entry} has no `name:` in its frontmatter")
    return match.group(1).strip("\"'")

#: Maximum modes allowed by the project's rules (no world access).
_DIR_MODE: Final[int] = 0o770
_FILE_MODE: Final[int] = 0o660

#: Project-relative destinations. ``openclaw`` is resolved separately (needs DEST).
_PROJECT_TARGETS: Final[dict[str, Path]] = {
    "cursor": Path(".cursor") / "skills",
    "codex": Path(".agents") / "skills",
    "claude": Path(".claude") / "skills",
    "opencode": Path(".opencode") / "skills",
}

_OPENCLAW_TARGET: Final[str] = "openclaw"

#: Home-directory install that requires the path to be stated. ``personal`` defaults to
#: ``~``; this target refuses to, so "which home directory" is always a decision made
#: out loud — a machine with several users, or a shared volume, otherwise picks one
#: silently.
_HOME_EXPLICIT_TARGET: Final[str] = "home"

#: User-level destinations (whole machine, every project). Verified against each
#: tool's docs: Cursor reads ``~/.cursor/skills``, Codex and the tool-agnostic
#: standard use ``~/.agents/skills``, Claude Code uses ``~/.claude/skills``, and
#: OpenCode's own global root is ``~/.config/opencode/skills`` (not
#: ``~/.opencode/skills``, a common error in third-party guides).
_HOME_TARGETS: Final[dict[str, Path]] = {
    "personal": Path(".cursor") / "skills",
    "personal-agents": Path(".agents") / "skills",
    "personal-claude": Path(".claude") / "skills",
    "personal-opencode": Path(".config") / "opencode" / "skills",
    "personal-openclaw": Path(".openclaw") / "skills",
}

_ALL_TARGETS: Final[tuple[str, ...]] = (*_PROJECT_TARGETS, *_HOME_TARGETS, _HOME_EXPLICIT_TARGET, _OPENCLAW_TARGET)

#: Never copied into a destination: VCS metadata and bytecode caches are not skill
#: content. ``.git`` matters most: a clone passed as the source must not ship its
#: history into the installed skill.
_EXCLUDED_DIRS: Final[frozenset[str]] = frozenset({".git", "__pycache__", ".pytest_cache"})

#: Installer-side and developer-side files: tools for getting the skill onto a machine and
#: for testing it there, not part of the skill the agent reads. Copying them is how an
#: installed skill ends up with its own redundant installer, and a second copy of install.py
#: would then copy a third. ``tests`` and ``pyproject.toml`` belong to the plugin's own
#: development (TASK-085 stage 1 moved the suite in): shipping the suite into a project
#: puts dead weight in the agent's tree and a second copy to keep in step.
_EXCLUDED_FILES: Final[frozenset[str]] = frozenset(
    {"install.py", "install.sh", "pyproject.toml", "conftest.py"}
)

#: Directories never copied: caches, VCS metadata, and the plugin's own test suite.
_EXCLUDED_TOP_DIRS: Final[frozenset[str]] = frozenset({"tests"})


@dataclass(frozen=True)
class InstallPlan:
    """One planned destination for the skill.

    Attributes:
        target: Target id (``cursor``, ``codex``, ...).
        source: The skill directory.
        destination: Absolute destination directory.
        project: Project root for project-scope targets; empty for user-scope ones,
            which have no project to attach a hook to.
    """

    target: str
    source: Path
    destination: Path
    project: Path | None = None


def _fail(message: str) -> NoReturn:
    """Print an error to stderr and exit non-zero (fail closed)."""
    print(f"install: {message}", file=sys.stderr)
    raise SystemExit(2)


def _project_root(explicit: str | None) -> Path | None:
    """Find the git checkout this skill belongs to.

    Walks up from the skill directory and returns the **outermost** ancestor that
    contains ``.git``. That distinction matters: inside a project the skill is a
    submodule with its own gitlink, so the nearest ``.git`` is the skill, not the
    project.

    Args:
        explicit: ``--project`` value, or ``None``.

    Returns:
        Absolute project root, or ``None`` for a standalone clone.
    """
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            _fail(f"--project {root} is not a directory")
        return root

    found: Path | None = None
    for parent in SKILL_DIR.parents:
        if (parent / ".git").exists():
            found = parent
    return found


def _explicit_home(raw: str) -> Path:
    """Validate the ``--home`` value for the ``home`` target.

    Args:
        raw: Path from argv.

    Returns:
        Absolute home directory.

    Raises:
        SystemExit: When the path does not exist or is not a directory.
    """
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        _fail(f"--home {root} is not an existing directory")
    return root


def _openclaw_root(dest: str | None) -> Path:
    """Resolve the OpenClaw workspace root.

    Args:
        dest: Explicit ``--dest`` value, or ``None``.

    Returns:
        Absolute path to the directory that contains ``skills/``.

    Raises:
        SystemExit: If ``dest`` is absent or does not exist as a directory.
    """
    if not dest:
        _fail("target 'openclaw' requires --dest <workspace>; refusing to guess a home directory")
    root = Path(dest).expanduser().resolve()
    if not root.is_dir():
        _fail(f"--dest {root} is not an existing directory")
    return root


def build_plans(
    targets: list[str],
    dest: str | None = None,
    project: str | None = None,
    home: str | None = None,
) -> list[InstallPlan]:
    """Build the install plan for the requested targets.

    Args:
        targets: Target ids; ``all`` expands to every project-scope target.
        dest: Destination root for ``openclaw``.
        project: Explicit project root for project-scope targets.
        home: Explicit home directory for the ``home`` target. Mandatory: the whole
            point of the target is that the path is stated rather than assumed.

    Returns:
        One :class:`InstallPlan` per resolved target, in a stable order.

    Raises:
        SystemExit: On an unknown target, a project-scope target with no project, or
            ``home`` without ``--home``.
    """
    if not (SKILL_DIR / "SKILL.md").is_file():
        _fail(f"{SKILL_DIR} has no SKILL.md; this script must live inside a skill")

    requested: list[str] = []
    for target in targets:
        if target == "all":
            # ``all`` covers project-scope targets only: they are the ones a repo
            # carries. Home and openclaw write outside the project and need an
            # explicit choice, so they are never implied.
            requested.extend(_PROJECT_TARGETS)
        else:
            requested.append(target)

    unknown = [t for t in requested if t not in _ALL_TARGETS]
    if unknown:
        _fail(
            f"unknown target(s) {', '.join(sorted(set(unknown)))}; "
            f"known: {', '.join(_ALL_TARGETS)}"
        )

    if _HOME_EXPLICIT_TARGET in requested and not home:
        _fail(
            "--target home requires --home <path>. The path is deliberately not defaulted to "
            "~: on a machine with several users or a shared volume, a silent default installs "
            "the skill into a home nobody chose."
        )

    needs_project = [t for t in requested if t in _PROJECT_TARGETS]
    skill_id = _skill_id()
    root = _project_root(project) if needs_project else None
    if needs_project and root is None:
        _fail(
            "project-scope target(s) "
            f"{', '.join(sorted(set(needs_project)))} need a project, and this skill is not "
            "inside a git checkout. Pass --project <dir>, or use a personal target to install "
            "for every project."
        )

    plans: list[InstallPlan] = []
    seen: set[str] = set()
    for target in requested:
        if target in seen:
            continue
        seen.add(target)

        if target == _OPENCLAW_TARGET:
            destination = _openclaw_root(dest) / "skills" / skill_id
        elif target == _HOME_EXPLICIT_TARGET:
            assert home is not None  # guarded above
            destination = _explicit_home(home) / ".cursor" / "skills" / skill_id
        elif target in _HOME_TARGETS:
            destination = Path.home() / _HOME_TARGETS[target] / skill_id
        else:
            assert root is not None  # guarded above
            destination = root / _PROJECT_TARGETS[target] / skill_id

        plans.append(
            InstallPlan(
                target=target,
                source=SKILL_DIR,
                destination=destination,
                project=root if target in _PROJECT_TARGETS else None,
            )
        )

    return plans


def _iter_source_files(source: Path) -> list[Path]:
    """Return every installable file under ``source``, relative to it, sorted.

    Args:
        source: The skill directory.

    Returns:
        Relative paths of files to copy, sorted for a stable plan.
    """
    files: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if _EXCLUDED_DIRS.intersection(relative.parts):
            continue
        if relative.parts[0] in _EXCLUDED_TOP_DIRS:
            continue
        if relative.name in _EXCLUDED_FILES and relative.parent == Path("."):
            continue
        files.append(relative)
    return sorted(files)


def _ensure_dir(path: Path) -> None:
    """Create ``path`` and clamp its mode to the policy.

    Only the deepest directory is clamped here: ``mkdir(parents=True)`` also creates every
    ancestor, and those keep the process umask (0755 on macOS). ``_clamp_tree`` fixes the rest
    once the copy is done, which is the only place that knows the install root.

    Args:
        path: Directory to create (parents included).
    """
    path.mkdir(parents=True, exist_ok=True)
    current = path.stat().st_mode & 0o777
    if current & 0o007:
        os.chmod(path, current & _DIR_MODE)


def _clamp_tree(root: Path) -> None:
    """Apply the permission policy to every directory and file under ``root``.

    Walking the finished tree is what keeps the policy true when the plugin grows: each new
    directory used to need its own ``_ensure_dir`` call, and the one nobody added shipped
    world-readable. ``lib/`` was found at 0755 exactly that way.

    Args:
        root: Installed skill directory.
    """
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        mode = path.stat().st_mode & 0o777
        if path.is_dir():
            clamped = mode & _DIR_MODE
        else:
            clamped = mode & (_FILE_MODE | 0o100)
        if clamped != mode:
            os.chmod(path, clamped)
    root_mode = root.stat().st_mode & 0o777
    if root_mode & 0o007:
        os.chmod(root, root_mode & _DIR_MODE)


def _ensure_file_mode(path: Path) -> None:
    """Clamp a file mode so no world bits survive, keeping the owner's exec bit.

    A hook script that loses ``+x`` is an install that looks correct and fails on the
    first tool call — Cursor reports the hook as failed, and with ``failClosed: true``
    that blocks the agent. So the owner bits are carried over from the source file
    rather than reset to a read-write default.

    Args:
        path: File just written.
    """
    current = path.stat().st_mode & 0o777
    # 0o660 has no exec bit, so masking alone strips +x from hook wrappers. The
    # owner-only bits are allowed through; group and world stay clamped (§5.9).
    allowed = _FILE_MODE | 0o100
    clamped = current & allowed
    if current != clamped:
        os.chmod(path, clamped)


def _copy_file(source: Path, target: Path) -> None:
    """Copy one file, preserving the owner's exec bit within the permission policy.

    Args:
        source: File in the skill directory.
        target: Destination file.
    """
    shutil.copy2(source, target)
    _ensure_file_mode(target)
    if source.stat().st_mode & 0o100 and not target.stat().st_mode & 0o100:
        # Scripts (hook wrappers, runner entry points) must stay executable.
        os.chmod(target, target.stat().st_mode | 0o100)


def _check_destination(destination: Path) -> None:
    """Refuse to install over a non-directory or through a symlinked root.

    Args:
        destination: Intended destination directory.

    Raises:
        SystemExit: If the destination exists as a file, or its parent is a symlink
            (writing through it would land outside the intended tree).
    """
    if destination.exists() and not destination.is_dir():
        _fail(f"destination {destination} exists and is not a directory")
    if destination.parent.is_symlink():
        _fail(f"destination parent {destination.parent} is a symlink; refusing to follow it")


def _prune_stale(destination: Path, expected: set[Path], dry_run: bool) -> list[Path]:
    """Remove files in ``destination`` that no longer exist in the source.

    Args:
        destination: Installed skill directory.
        expected: Relative paths that must survive.
        dry_run: When true, report without deleting.

    Returns:
        Relative paths that were (or would be) removed.
    """
    if not destination.is_dir():
        return []

    stale: list[Path] = []
    for path in sorted(destination.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(destination)
        if relative not in expected:
            stale.append(relative)
            if not dry_run:
                path.unlink()

    if not dry_run:
        for directory in sorted(
            (p for p in destination.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()

    return stale


#: Config file each harness reads for hooks, relative to the project root, and the
#: template that fills it. Claude Code and Codex read a nested shape; Cursor reads a
#: flat one. All three point at the same wrapper, so the gate has one entry point.
_PROJECT_HOOK_FILES: Final[dict[str, tuple[Path, str]]] = {
    "cursor": (Path(".cursor") / "hooks.json", "cursor.hooks.json"),
    "claude": (Path(".claude") / "settings.json", "claude.settings.json"),
    "codex": (Path(".codex") / "hooks.json", "codex.hooks.json"),
}


def _gate_wrapper(plan: InstallPlan) -> Path:
    """Path to the installed wrapper script for one plan.

    Args:
        plan: Target descriptor.

    Returns:
        Absolute path to ``hooks/run-gate.sh`` inside the installed skill.
    """
    return plan.destination / "hooks" / "run-gate.sh"


def _hook_marker(plan: InstallPlan) -> str:
    """Substring that identifies this skill's own entries in any harness config.

    Built from the installed skill's relative location rather than a fixed prefix, and it names
    the ``hooks/`` directory rather than one script inside it: a plugin wires more than one entry
    (the gate, and the session-start status), and a marker tied to the gate left the status entry
    pointing at a deleted file after `--uninstall` — a fail-closed hook on every tool call, which
    is the exact failure this marker exists to prevent.

    Args:
        plan: Target descriptor.

    Returns:
        The directory fragment used for filtering.
    """
    return f"{plan.destination.parent.name}/{plan.destination.name}/hooks/"


def _render_template(template: Path, plan: InstallPlan) -> dict[str, Any]:
    """Read a hook template, refusing anything that would need a baked-in path.

    Each template references the installed wrapper through a variable its harness
    documents, so the same file works in any project on any machine:

    * Cursor — ``$CURSOR_PROJECT_DIR`` (documented env var for project hooks);
    * Claude Code — ``${CLAUDE_PROJECT_DIR}`` (documented in the hooks reference);
    * Codex — no project variable exists, so the repository root is resolved with
      ``git rev-parse --show-toplevel`` at run time, which is what Codex's own hook
      examples do.

    The ledger is never substituted: it does not exist at install time and lives in a
    per-project place, so the gate discovers it from the project contract.

    Args:
        template: Template file inside ``hooks/``.
        plan: Target descriptor, used only for the error message.

    Returns:
        Parsed JSON, unchanged.

    Raises:
        SystemExit: When a template asks for a value that cannot be portable.
    """
    del plan
    text = template.read_text(encoding="utf-8")
    if "__LEDGER__" in text:
        raise SystemExit(
            f"{template.name} still asks for __LEDGER__. The ledger is discovered from the "
            "project contract, so no absolute path may be baked into a hook template."
        )
    if "__GATE_WRAPPER__" in text:
        raise SystemExit(
            f"{template.name} uses __GATE_WRAPPER__, which would bake an absolute install "
            "path. Use the harness's project-directory variable instead."
        )
    return json.loads(text)


def _merge_hook_file(hook_file: Path, rendered: dict[str, Any], marker: str, *, dry_run: bool) -> None:
    """Write a harness hook file, keeping entries that are not this skill's.

    The shapes differ per harness, so the merge is deliberately narrow: it walks the
    ``hooks`` mapping, keeps every event list intact except the one this skill owns, and
    drops only entries whose JSON mentions this skill's path. Replacing the file would
    delete hooks the installer cannot see.

    Args:
        hook_file: Destination config file.
        rendered: Template with placeholders substituted.
        marker: Substring identifying this skill's own entries.
        dry_run: When true, report without writing.
    """
    existing: dict[str, Any] = {}
    if hook_file.is_file():
        try:
            existing = json.loads(hook_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"{hook_file} is not valid JSON ({exc}); fix it by hand rather than risk "
                "losing hooks this installer does not know about"
            ) from exc
        if not isinstance(existing, dict):
            raise SystemExit(f"{hook_file} is not a JSON object; refusing to merge into it")

    merged = {**existing}
    merged.update({k: v for k, v in rendered.items() if k != "hooks"})
    existing_hooks = existing.get("hooks") if isinstance(existing.get("hooks"), dict) else {}
    new_hooks = rendered.get("hooks", {})

    hooks: dict[str, Any] = {**existing_hooks}
    for event, new_entries in new_hooks.items():
        old = existing_hooks.get(event)
        old_list = old if isinstance(old, list) else []
        # Nested shapes (Claude, Codex) wrap the command in `hooks: [...]`; the marker
        # is inside that JSON either way, so one filter covers both shapes.
        kept = [item for item in old_list if marker not in json.dumps(item)]
        hooks[event] = [*new_entries, *kept]
    merged["hooks"] = hooks

    print(f"    {'would write' if dry_run else 'write'} {hook_file} ({', '.join(new_hooks) or 'hooks'})")
    if dry_run:
        return
    _ensure_dir(hook_file.parent)
    hook_file.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    _ensure_file_mode(hook_file)


def _write_project_hooks(plan: InstallPlan, *, dry_run: bool) -> None:
    """Wire the gate into every harness that reads a project-level hook file.

    One entry point (``hooks/run-gate.sh``) for all of them: the gate already speaks
    each harness's payload shape, so the difference between harnesses lives in the
    config file, not in the logic. That is also why a fix in the gate reaches all five
    at once.

    Args:
        plan: Target descriptor.
        dry_run: When true, report the action without writing.
    """
    if plan.project is None:
        return
    # The marker is derived from the installed location, so it recognises this skill's
    # entry whatever directory the harness installs skills into.
    marker = _hook_marker(plan)
    written = 0

    for target, (relative, template_name) in _PROJECT_HOOK_FILES.items():
        template = SKILL_DIR / "hooks" / template_name
        if not template.is_file():
            continue
        # `all` is expanded into concrete targets before plans are built, so matching
        # the plan's own target is enough.
        if target == plan.target:
            rendered = _render_template(template, plan)
            _merge_hook_file(plan.project / relative, rendered, marker, dry_run=dry_run)
            written += 1

    if written == 0:
        print(
            f"    no hook file for target {plan.target!r}: the wrapper was installed, but the "
            "gate is not wired for it (supported: cursor, claude, codex)"
        )


def _write_project_rules(plan: InstallPlan, *, dry_run: bool) -> None:
    """Give the project's harness the rule that sends the agent to `bugfixer doctor`.

    Spec §18.3 makes a rules file the main initialisation channel, because `sessionStart` does not
    deliver its context in Cursor (§18.4). The rule is written for the harness being installed into
    and merged into files that belong to the project, so nothing the project wrote is lost and a
    re-install does not stack a second copy.

    Which file each harness gets is decided in :mod:`bugfixer.rules`, not here: `init` places the
    same rule for every harness, and two copies of that mapping would eventually disagree.

    Args:
        plan: Target descriptor; only project-scope plans have a project to write into.
        dry_run: When true, report the action without writing.
    """
    if plan.project is None:
        return

    prefix = "would write" if dry_run else "write"
    for merged in rules_module.for_harness(plan.project, plan.target, dry_run=dry_run):
        if merged.action == "unchanged":
            print(f"    keep {merged.path} (rule already present)")
        elif merged.action == "kept":
            print(f"    keep {merged.path} (already imports AGENTS.md)")
        else:
            print(f"    {prefix} {merged.path} ({merged.action}: project rule for {plan.target})")


def install(plan: InstallPlan, *, dry_run: bool) -> list[Path]:
    """Copy the skill into one destination, pruning stale files.

    Args:
        plan: Target descriptor.
        dry_run: When true, report actions without touching the filesystem.

    Returns:
        Relative paths that were (or would be) pruned.
    """
    _check_destination(plan.destination)

    files = _iter_source_files(plan.source)
    print(f"{'[dry-run] ' if dry_run else ''}==> {plan.target}: {plan.destination}")
    for relative in files:
        print(f"    {'would copy' if dry_run else 'copy'} {relative}")

    stale = _prune_stale(plan.destination, set(files), dry_run)
    for relative in stale:
        print(f"    {'would prune' if dry_run else 'prune'} {relative}")

    if dry_run:
        _write_project_hooks(plan, dry_run=True)
        _write_project_rules(plan, dry_run=True)
        _seed_contract(plan, dry_run=True)
        return stale

    for relative in files:
        target = plan.destination / relative
        _ensure_dir(target.parent)
        _copy_file(plan.source / relative, target)

    _write_project_hooks(plan, dry_run=False)
    _write_project_rules(plan, dry_run=False)
    _seed_contract(plan, dry_run=False)
    _clamp_tree(plan.destination)

    return stale


def _seed_contract(plan: InstallPlan, *, dry_run: bool) -> None:
    """Write a starting contract into a project that has none.

    A project install wires the gate, and the gate refuses work without a contract. Leaving the
    project without one would therefore mean "installed, and now everything is refused" — the
    plugin would be broken out of the box by its own safety rule. So the install writes the same
    scaffold `bugfixer init` writes.

    An existing contract is never touched: it holds the project's decisions, and a re-install is
    not a reason to discard them.

    Args:
        plan: Target descriptor; only project-scope plans seed a contract.
        dry_run: When true, report the action without writing.
    """
    if plan.project is None:
        return
    target = plan.project / CONTRACT_NAME
    if target.exists():
        print(f"    keep {CONTRACT_NAME} (already present; edit it and run `bugfixer doctor`)")
        return
    print(f"    {'would write' if dry_run else 'write'} {CONTRACT_NAME} (fill in the `<…>` fields)")
    if dry_run:
        return
    contract_module.write_created(target, contract_module.scaffold(plan.project))


def _strip_hook_entries(hook_file: Path, marker: str, *, dry_run: bool) -> bool:
    """Remove this skill's entry from one harness config, keeping everything else.

    Args:
        hook_file: Harness config file.
        marker: Substring identifying this skill's entries.
        dry_run: When true, report without writing.

    Returns:
        ``True`` when an entry was found (or would be removed).
    """
    if not hook_file.is_file():
        return False
    try:
        payload = json.loads(hook_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Leftover install with a hand-broken config: report, do not destroy it.
        print(f"    {hook_file} is not valid JSON; remove the entry by hand", file=sys.stderr)
        return False
    if not isinstance(payload, dict):
        return False
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False

    removed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = [item for item in entries if marker not in json.dumps(item)]
        if len(kept) != len(entries):
            removed = True
            hooks[event] = kept

    if not removed:
        return False

    # Drop event lists that became empty: an empty `preToolUse: []` is noise that
    # suggests a hook is configured when none is.
    payload["hooks"] = {event: items for event, items in hooks.items() if items != []}

    print(f"    {'would clean' if dry_run else 'clean'} {hook_file}")
    if not dry_run:
        if payload["hooks"]:
            hook_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        else:
            hook_file.unlink()
    return True


def uninstall(plan: InstallPlan, *, dry_run: bool) -> int:
    """Remove an installed skill and every hook entry that points at it.

    The hook entry is the dangerous half: a leftover command pointing at a deleted
    wrapper is a ``failClosed`` hook that blocks every tool call, and the agent reports
    a broken guard rather than a missing skill. So both halves are removed together, and
    a destination that does not exist is not an error — uninstalling twice is harmless.

    Args:
        plan: Target descriptor.
        dry_run: When true, report without deleting.

    Returns:
        Number of things removed (directory + hook files).
    """
    removed = 0
    marker = _hook_marker(plan)

    print(f"{'[dry-run] ' if dry_run else ''}==> uninstall {plan.target}: {plan.destination}")
    if plan.destination.is_dir():
        print(f"    {'would remove' if dry_run else 'remove'} {plan.destination}")
        if not dry_run:
            shutil.rmtree(plan.destination)
        removed += 1
    else:
        print("    skill directory not present")

    if plan.project is not None:
        for relative, _ in _PROJECT_HOOK_FILES.values():
            if _strip_hook_entries(plan.project / relative, marker, dry_run=dry_run):
                removed += 1
        removed += _remove_project_rules(plan, dry_run=dry_run)

    return removed


def _remove_project_rules(plan: InstallPlan, *, dry_run: bool) -> int:
    """Remove the project rule this plugin added, and nothing else.

    A rule left behind after removal is worse than the hook: the agent keeps being told to run
    `bugfixer doctor`, the plugin is gone, and the command fails — a plugin that appears installed
    and is not. So removal is paired with installation, exactly as for hook entries.

    Args:
        plan: Target descriptor; only project-scope plans wrote a rule.
        dry_run: When true, report without writing.

    Returns:
        Number of files touched.
    """
    if plan.project is None:
        return 0
    root = plan.project
    prefix = "would clean" if dry_run else "clean"
    touched = 0

    if plan.target == "cursor":
        merged = rules_module.remove_owned(
            root / rules_module.CURSOR_RULE_FILE, dry_run=dry_run
        )
    else:
        merged = rules_module.strip(root / rules_module.AGENTS_FILE, dry_run=dry_run)
        if plan.target == "claude":
            import_merged = rules_module.strip(
                root / rules_module.CLAUDE_FILE, dry_run=dry_run
            )
            if import_merged.action == "updated":
                print(f"    {prefix} {import_merged.path} (bug-fixer import)")
                touched += 1

    if merged.action in {"removed", "updated"}:
        print(f"    {prefix} {merged.path} (bug-fixer project rule)")
        touched += 1
    return touched

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="install.py",
        description=f"Install the {_skill_id()} skill into agent environments.",
    )
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        help=(
            f"destination environment: {', '.join(_ALL_TARGETS)}, or all "
            "(all = every project-scope target; openclaw needs --dest)"
        ),
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="workspace root for --target openclaw; its 'skills/' subdirectory is used",
    )
    parser.add_argument(
        "--project",
        default=None,
        help=(
            "project root for project-scope targets; defaults to the outermost git "
            "checkout containing this skill"
        ),
    )
    parser.add_argument(
        "--home",
        default=None,
        help=(
            "home directory for --target home; mandatory, because a silent default of ~ "
            "installs into a home nobody chose"
        ),
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help=(
            "remove the installed skill and this skill's hook entries; a removal that "
            "left the hook behind would block every tool call via failClosed"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    plans = build_plans(list(args.target), args.dest, args.project, args.home)

    if args.dry_run:
        print(f"[dry-run] source: {SKILL_DIR}")

    if args.uninstall:
        total = 0
        for plan in plans:
            total += uninstall(plan, dry_run=args.dry_run)
        verb = "would remove" if args.dry_run else "removed"
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"{prefix}{verb} {total} item(s) for {_skill_id()}")
        return 0

    for plan in plans:
        install(plan, dry_run=args.dry_run)

    verb = "would install" if args.dry_run else "installed"
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{verb} {_skill_id()} into {len(plans)} target(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
