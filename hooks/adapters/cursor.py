"""Cursor: `sessionStart` → ``additional_context``.

Registered by Cursor in `hooks/hooks-cursor.json` (plugin) or `hooks/cursor.hooks.json`
(project). This file is the only place that knows Cursor's spelling.

**Best-effort, and knowingly so.** Cursor runs the hook, accepts the JSON and logs it, and the
model does not receive ``additional_context`` (vendor bug since April 2026, confirmed by Cursor
staff, no ETA; §18.4). No requirement depends on delivery: the project rule in
`.cursor/rules/bug-fixer.mdc` carries the same instruction, and the gate refuses the first
unauthorised write regardless. When the vendor fixes the channel this file needs no change —
only the message gains precision (§18.7).

Environment sniffing here is limited to the project directory. The adapter is chosen by Cursor,
not by looking at variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

from adapters import one_key

HARNESS: Final[str] = "cursor"


def project_dir(event: dict[str, Any] | None = None) -> Path:
    """The project Cursor is talking about.

    Args:
        event: Session payload, which may carry ``cwd`` or ``workspace_roots``.

    Returns:
        Absolute project directory: the event's directory, else ``CURSOR_PROJECT_DIR``, else the
        working directory.
    """
    for key in ("cwd",):
        value = (event or {}).get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()
    roots = (event or {}).get("workspace_roots")
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str) and root.strip():
                return Path(root).expanduser().resolve()
    raw = os.environ.get("CURSOR_PROJECT_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def render_context(message: str) -> dict[str, Any]:
    """Lay a status message into Cursor's session-start response.

    Args:
        message: Text from the shared readiness check; never empty here (an empty message means
            the project is ready, and the caller then says nothing at all).

    Returns:
        Cursor's JSON payload, with exactly one field.
    """
    return one_key({"additional_context": message})
