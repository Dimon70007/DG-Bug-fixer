"""One adapter per harness: the harness picks the file, the file lays the answer out.

Spec §18.2 puts the split here. The core (`bugfixer.readiness`) decides **what to say**; an
adapter decides **how this harness spells it**. The format is not ours —
``additional_context``, ``hookSpecificOutput.additionalContext``, ``additionalContext`` and
``{block, blockReason}`` are four vendor contracts — so a single "our format" would have to be
translated back into three of them anyway, in code that nobody can debug for one harness at a
time.

Two things are deliberately independent:

* **Which adapter runs** is chosen by the harness, which registered its own file in its own hook
  config. There is no dispatcher that inspects the environment and guesses: an error in such a
  dispatcher reads as "the hook is silent", not as a failure.
* **What the adapter reads from the environment** (`CURSOR_PROJECT_DIR`, `CLAUDE_PROJECT_DIR`,
  cwd) *is* sniffing, and it is wanted: it is the only cheap way to learn, from inside a harness,
  which project is being talked about. A wrong value is visible in one small file — and invariant
  42 still holds: the project comes from the working directory, never from where the plugin is
  installed.

Every adapter returns a mapping with **exactly one key**. Claude Code reads both spellings of its
own field without de-duplicating, so a response carrying two would inject the context twice.
"""

from __future__ import annotations

from typing import Final

#: Harness ids that have an adapter file. Any other value is a wiring error, not a default.
HARNESSES: Final[tuple[str, ...]] = ("cursor", "claude", "codex", "opencode", "openclaw")


class UnsupportedSessionContext(RuntimeError):
    """This harness cannot accept injected session context.

    Raised instead of returning ``{}``: an empty response looks exactly like "the project is
    ready", which is the one answer that must never be given by accident (§0).
    """


def one_key(payload: dict[str, object]) -> dict[str, object]:
    """Assert an adapter's own promise before it leaves the process.

    Args:
        payload: The rendered response.

    Returns:
        The same payload.

    Raises:
        AssertionError: When the payload has anything but one key. A programming error in an
            adapter, never a runtime condition.
    """
    if len(payload) != 1:
        raise AssertionError(f"adapter returned {sorted(payload)}; exactly one field is allowed")
    return payload
