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
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

#: This file lives in the skill directory, so the source is unambiguous.
SKILL_DIR: Final[Path] = Path(__file__).resolve().parent


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

_ALL_TARGETS: Final[tuple[str, ...]] = (*_PROJECT_TARGETS, *_HOME_TARGETS, _OPENCLAW_TARGET)

#: Never copied into a destination: VCS metadata and bytecode caches are not skill
#: content. ``.git`` matters most: a clone passed as the source must not ship its
#: history into the installed skill.
_EXCLUDED_DIRS: Final[frozenset[str]] = frozenset({".git", "__pycache__", ".pytest_cache"})

#: Installer-side files: tools for getting the skill onto a machine, not part of the
#: skill the agent reads. Copying them is how an installed skill ends up with its own
#: redundant installer, and a second copy of install.py would then copy a third.
_EXCLUDED_FILES: Final[frozenset[str]] = frozenset({"install.py", "install.sh"})


@dataclass(frozen=True)
class InstallPlan:
    """One planned destination for the skill.

    Attributes:
        target: Target id (``cursor``, ``codex``, ...).
        source: The skill directory.
        destination: Absolute destination directory.
    """

    target: str
    source: Path
    destination: Path


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
) -> list[InstallPlan]:
    """Build the install plan for the requested targets.

    Args:
        targets: Target ids; ``all`` expands to every project-scope target.
        dest: Destination root for ``openclaw``.
        project: Explicit project root for project-scope targets.

    Returns:
        One :class:`InstallPlan` per resolved target, in a stable order.

    Raises:
        SystemExit: On an unknown target or a project-scope target with no project.
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
        elif target in _HOME_TARGETS:
            destination = Path.home() / _HOME_TARGETS[target] / skill_id
        else:
            assert root is not None  # guarded above
            destination = root / _PROJECT_TARGETS[target] / skill_id

        plans.append(InstallPlan(target=target, source=SKILL_DIR, destination=destination))

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
        if relative.name in _EXCLUDED_FILES and relative.parent == Path("."):
            continue
        files.append(relative)
    return sorted(files)


def _ensure_dir(path: Path) -> None:
    """Create ``path`` and clamp its mode to the policy.

    Args:
        path: Directory to create (parents included).
    """
    path.mkdir(parents=True, exist_ok=True)
    current = path.stat().st_mode & 0o777
    if current & 0o007:
        os.chmod(path, current & _DIR_MODE)


def _ensure_file_mode(path: Path) -> None:
    """Clamp a file mode so no world bits survive.

    Args:
        path: File just written.
    """
    current = path.stat().st_mode & 0o777
    if current & 0o007:
        os.chmod(path, current & _FILE_MODE)


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
        return stale

    for relative in files:
        target = plan.destination / relative
        _ensure_dir(target.parent)
        shutil.copy2(plan.source / relative, target)
        _ensure_file_mode(target)

    return stale


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
    plans = build_plans(list(args.target), args.dest, args.project)

    if args.dry_run:
        print(f"[dry-run] source: {SKILL_DIR}")

    for plan in plans:
        install(plan, dry_run=args.dry_run)

    verb = "would install" if args.dry_run else "installed"
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{verb} {_skill_id()} into {len(plans)} target(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
