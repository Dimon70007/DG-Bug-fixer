"""Claude Code: `SessionStart` → ``hookSpecificOutput.additionalContext``.

Registered in `hooks/claude.settings.json` (project install) or by the plugin's own hook config.

Claude Code accepts **both** ``hookSpecificOutput.additionalContext`` and a bare top-level
``additionalContext`` and does not de-duplicate them, so a response carrying both injects the text
twice. Hence :func:`adapters.one_key`: one top-level field, always.

The nested ``hookEventName`` is not a second spelling of the context — it is how Claude Code ties
the payload to the event that produced it, and omitting it makes the hook fail to register rather
than deliver.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

from adapters import one_key

HARNESS: Final[str] = "claude"

#: The event this payload answers. Claude Code matches the value against the hook it fired.
EVENT: Final[str] = "SessionStart"


def project_dir(event: dict[str, Any] | None = None) -> Path:
    """The project Claude Code is talking about.

    Args:
        event: Session payload, which may carry ``cwd``.

    Returns:
        Absolute project directory: the event's directory, else ``CLAUDE_PROJECT_DIR``, else the
        working directory. The contract search walks up from it, so a subdirectory still finds the
        project (§18.5).
    """
    for key in ("cwd",):
        value = (event or {}).get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()
    raw = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def render_context(message: str) -> dict[str, Any]:
    """Lay a status message into Claude Code's session-start response.

    Args:
        message: Text from the shared readiness check.

    Returns:
        Claude Code's JSON payload; exactly one top-level field.
    """
    return one_key(
        {"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": message}}
    )
