"""OpenClaw: ``{ block, blockReason }`` — the pair its typed hooks return.

Registered by the plugin's runtime hooks (`hooks/openclaw-plugin.ts` for tool calls; a session
hook here). OpenClaw's typed `before_tool_call` answers with ``{ block: true, blockReason }``, and
that is also the only shape its session hook can return that reaches the agent, so the status
message travels as a block reason rather than as injected text.

**Two keys, on purpose.** :func:`adapters.one_key` guards the harnesses that accept two spellings
of the same field (Claude Code injects twice if both arrive). OpenClaw has no such ambiguity: the
``block`` flag is meaningless without the reason, and a reason without the flag blocks nothing. The
pair is one value in two parts, and the reason is never empty — an empty refusal is what §0 calls a
broken guard.

Environment sniffing is limited to the working directory: OpenClaw exposes no documented project
variable, and it may report ``cwd`` or ``workdir`` per event.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

HARNESS: Final[str] = "openclaw"


def project_dir(payload: dict[str, Any] | None = None) -> Path:
    """The project OpenClaw is talking about.

    Args:
        payload: Session event, which may carry ``cwd`` or ``workdir``.

    Returns:
        Absolute project directory: the event's directory, else the project variable, else the
        working directory. OpenClaw exposes no documented project variable of its own, so the
        event is the primary source and the variable is a courtesy.
    """
    for key in ("cwd", "workdir"):
        value = (payload or {}).get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()
    raw = os.environ.get("OPENCLAW_PROJECT_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def render_context(message: str) -> dict[str, Any]:
    """Lay a status message into OpenClaw's session response.

    Args:
        message: Text from the shared readiness check.

    Returns:
        ``{"block": True, "blockReason": message}``.

    Raises:
        ValueError: When the message is empty. A block with no reason reads to the agent as a
            broken guard, and the caller should stay silent instead of sending one.
    """
    if not message.strip():
        raise ValueError(
            "openclaw: refusing to send a block with an empty reason; say nothing when the "
            "project is ready"
        )
    return {"block": True, "blockReason": message}
