#!/usr/bin/env python3
"""Session-start status: one sentence when a project is not ready, silence when it is.

Invoked by a tiny shell entry (`hooks/session-start`), one per harness, so no Python is inlined
into bash (§5.4). The harness passes its own id and its event payload on stdin; this module picks
the adapter file the harness registered, asks the shared core what to say, and prints the adapter's
payload.

Three rules the code below is built around:

* **Silence is a valid answer.** A ready project produces no output at all (invariant 41). A hook
  that always says something is a hook that gets ignored.
* **Nothing here refuses.** The gate refuses; this only informs, and the gate remains the primary
  mechanism (§18.4). A crash here must not block work, so a failure exits ``0`` with a note on
  stderr rather than a non-zero code.
* **The project comes from the working directory.** Never from the plugin's install location
  (invariant 42): a status computed there would describe somebody else's repository.

Exit codes: ``0`` always, unless the entry is misused (no harness id) — then ``2``, because that is
a wiring error and must be visible.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Final

#: This file's directory: ``<plugin>/hooks``. The plugin root is its parent.
_HOOKS: Final[Path] = Path(__file__).resolve().parent
_PLUGIN_ROOT: Final[Path] = _HOOKS.parent
for _path in (str(_PLUGIN_ROOT / "lib"), str(_HOOKS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from adapters import HARNESSES, UnsupportedSessionContext  # noqa: E402
from bugfixer import readiness  # noqa: E402

EXIT_OK: Final[int] = 0
EXIT_MISUSED: Final[int] = 2


def _event() -> dict[str, Any]:
    """Read the harness payload from stdin.

    Empty stdin is ``{}``: a manual run, which reports on the working directory. Non-JSON is
    treated the same way rather than raised — this hook informs, and a corrupt payload is not a
    reason to withhold a status the project rule already asks for.

    Returns:
        Parsed payload, or an empty dict.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _emit(payload: dict[str, Any]) -> None:
    """Print one adapter payload as JSON.

    Args:
        payload: Adapter response.
    """
    print(json.dumps(payload, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """Report project readiness in one harness's format.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]`` and must name a harness.

    Returns:
        ``0`` on success, including when the project is ready or the check failed;
        ``2`` when no harness was named or the name is unknown.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(
            "session-start: which harness? expected one of " + ", ".join(HARNESSES),
            file=sys.stderr,
        )
        return EXIT_MISUSED
    harness = args[0].strip().lower()
    if harness not in HARNESSES:
        print(
            f"session-start: unknown harness {harness!r}; the harness registers its own adapter "
            f"and there is no default (known: {', '.join(HARNESSES)})",
            file=sys.stderr,
        )
        return EXIT_MISUSED

    event = _event()
    adapter = importlib.import_module(f"adapters.{harness}")
    project = adapter.project_dir(event)
    try:
        state = readiness.inspect(project)
        text = readiness.message(state)
        if not text:
            return EXIT_OK
        payload = adapter.render_context(text)
    except UnsupportedSessionContext as exc:
        print(f"session-start: {harness}: {exc}", file=sys.stderr)
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 — informing must never block work
        print(f"session-start: {harness}: check failed ({exc})", file=sys.stderr)
        return EXIT_OK
    _emit(payload)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
