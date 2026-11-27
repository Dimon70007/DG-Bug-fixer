"""OpenCode: no session-context channel, so the adapter refuses instead of inventing one.

OpenCode plugins intercept tool calls in-process (`tool.execute.before` in
`hooks/opencode-plugin.ts`) and throw to block. They have **no** hook that injects text into the
session, so there is nothing to lay a status message into. Returning ``{}`` would be
indistinguishable from "the project is ready", which is why this raises: the caller can then skip
the hook for this harness instead of reporting a clean run of nothing.

The project rule still reaches the agent here — OpenCode reads `AGENTS.md` (falling back to
`CLAUDE.md`), and that is invariant 41's main channel. This adapter exists so the five harness
files are symmetric and so the absence is stated where someone would look for it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

from adapters import UnsupportedSessionContext

HARNESS: Final[str] = "opencode"

#: Why there is no response, quoted in the refusal so the reason travels with the failure.
REASON: Final[str] = (
    "OpenCode has no session-context hook: its plugins block tool calls, they do not inject text. "
    "The project rule in AGENTS.md carries the instruction instead (spec §18.3)."
)


def project_dir(event: dict[str, Any] | None = None) -> Path:
    """The project OpenCode is talking about.

    Args:
        event: Session payload, which may carry ``cwd`` or ``directory``.

    Returns:
        Absolute project directory. OpenCode exposes no documented project variable, and its
        plugins run tool calls from the project, so the working directory is the project.
    """
    for key in ("cwd", "directory"):
        value = (event or {}).get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()
    raw = os.environ.get("OPENCODE_PROJECT_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def render_context(message: str) -> dict[str, Any]:
    """Refuse to render a response this harness cannot receive.

    Args:
        message: Text from the shared readiness check; ignored, because there is nowhere to put it.

    Raises:
        UnsupportedSessionContext: Always.
    """
    del message
    raise UnsupportedSessionContext(REASON)
