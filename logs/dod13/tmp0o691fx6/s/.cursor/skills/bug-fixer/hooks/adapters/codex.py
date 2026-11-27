"""Codex: session context → top-level ``additionalContext``.

Registered in `hooks/codex.hooks.json` (project install).

Codex documents no project-directory variable for hooks, so the project is resolved the way its
own hook examples resolve it: ``git rev-parse --show-toplevel`` from the working directory, then
the working directory itself. Baking an install-time path into the config would break the first
time the skill moved.

The field is flat, unlike Claude Code's: Codex reads ``additionalContext`` at the top level, and a
nested copy would be ignored — the same text in the wrong place is a silent no-op.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Final

from adapters import one_key

HARNESS: Final[str] = "codex"


def project_dir(event: dict[str, Any] | None = None) -> Path:
    """The project Codex is talking about.

    Args:
        event: Session payload, which may carry ``cwd``.

    Returns:
        Repository root from ``git rev-parse --show-toplevel``, or the working directory when the
        call is not inside a checkout. Silence on a failed git call is deliberate: a project with
        no git is a project, and refusing to describe it would be the wrong kind of loud.
    """
    for key in ("cwd",):
        value = (event or {}).get(key)
        if isinstance(value, str) and value.strip():
            return _repo_root(Path(value).expanduser())
    return _repo_root(Path.cwd())


def _repo_root(start: Path) -> Path:
    """Resolve the enclosing git checkout from a directory.

    Args:
        start: Directory to resolve from.

    Returns:
        Repository root, or ``start`` resolved when git cannot answer.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
            cwd=str(start),
        )
    except (OSError, subprocess.SubprocessError):
        return start.resolve()
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).expanduser().resolve()
    return start.resolve()


def render_context(message: str) -> dict[str, Any]:
    """Lay a status message into Codex's session response.

    Args:
        message: Text from the shared readiness check.

    Returns:
        Codex's JSON payload; exactly one field.
    """
    return one_key({"additionalContext": message})
