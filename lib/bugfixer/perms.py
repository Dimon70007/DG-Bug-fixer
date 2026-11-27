"""File modes for whatever the plugin writes into a project.

Repo rule §5.9: nothing the plugin writes may be readable, writable or executable by other users.
``Path.write_text`` inherits the process umask, which on macOS is ``022`` — so a generated file
lands ``0644``, and a generated directory ``0755``. Neither is a policy violation *by the project*,
but both are grants made by the plugin, which the rule forbids.

The distinction that matters: only paths the plugin **creates** are clamped. A file the project
already had keeps its own mode, because its permissions are the project's decision — silently
tightening someone's `AGENTS.md` would be a change they did not ask for and would not see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: Files: owner and group read/write, no execute, no world bits.
FILE_MODE: Final[int] = 0o660

#: Directories: owner and group read/write/traverse, no world bits.
DIR_MODE: Final[int] = 0o770


def write_created(path: Path, text: str) -> None:
    """Write a file, clamping the mode when the plugin is creating it.

    Args:
        path: Destination file; parents must exist.
        text: Full contents.
    """
    created = not path.exists()
    path.write_text(text, encoding="utf-8")
    if created:
        path.chmod(FILE_MODE)


def ensure_dir(path: Path) -> None:
    """Create a directory chain, clamping every level the plugin created.

    Only the new levels are clamped: ``mkdir(parents=True)`` also creates ancestors, and the ones
    that already existed belong to the project.

    Args:
        path: Directory to ensure.
    """
    missing: list[Path] = []
    for candidate in (path, *path.parents):
        if candidate.exists():
            break
        missing.append(candidate)
    path.mkdir(parents=True, exist_ok=True)
    for created in missing:
        created.chmod(DIR_MODE)
