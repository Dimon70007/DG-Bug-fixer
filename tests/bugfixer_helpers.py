"""Helpers shared by the bug-fixer test modules.

Two jobs:

* **Locate the module.** The tests live inside the plugin they test
  (``<plugin>/tests/``), so the module root is this file's parent's parent. In the parent
  repository they are reached through ``skills/bug-fixer/tests``, where the same
  expression still resolves to the module. One definition, not eleven copies of a path.

* **Record run state.** The state lives in an append-only journal (spec §5.4), so a test
  that needs a run in a given phase appends an ``advance`` record instead of writing a
  state file. Keeping that in one place stops the record shape from being re-invented in
  each module — a second copy would drift, and then a test would pass against a journal
  the gate cannot read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The plugin directory under test.
#:
#: Two layouts, one definition: inside the plugin (``<plugin>/tests/``) the parent of this
#: directory *is* the plugin; in the parent repository the tests live at ``<repo>/tests``,
#: and the plugin is at ``<repo>/skills/bug-fixer``. Decided by the marker every plugin tree
#: carries — ``SKILL.md`` — rather than by a hard-coded relative path, so a test file can be
#: moved between the two without being edited.
def _locate_skill_dir() -> Path:
    """Find the plugin directory from this file's location.

    Returns:
        The directory holding ``SKILL.md``.

    Raises:
        RuntimeError: When neither layout has a ``SKILL.md`` — a broken checkout, and
            guessing a path would make every path test pass against nothing.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, here.parent / "skills" / "bug-fixer"):
        if (candidate / "SKILL.md").is_file():
            return candidate
    raise RuntimeError(f"no SKILL.md in {here.parent} or {here.parent / 'skills' / 'bug-fixer'}")


SKILL_DIR: Path = _locate_skill_dir()

HOOKS: Path = SKILL_DIR / "hooks"


def _locate_project_root() -> Path:
    """The git checkout this plugin belongs to.

    The *outermost* ancestor with ``.git``, matching what the installer does: inside a
    host project the plugin is a submodule with its own gitlink, so the nearest ``.git``
    is the plugin, not the project. A standalone clone is its own project.

    Returns:
        Absolute project root, or the plugin directory when nothing encloses it.
    """
    found = SKILL_DIR
    for parent in SKILL_DIR.parents:
        if (parent / ".git").exists():
            found = parent
    return found


PROJECT_ROOT: Path = _locate_project_root()


def journal_path(ledger: Path) -> Path:
    """Journal path for a task file.

    Args:
        ledger: Task file.

    Returns:
        ``<task file>.bugfixer.jsonl`` beside the task file.
    """
    return ledger.with_name(ledger.name + ".bugfixer.jsonl")


def append(ledger: Path, **record: Any) -> dict[str, Any]:
    """Append one raw record to the journal.

    Args:
        ledger: Task file.
        **record: Fields of the record; ``at`` and ``why`` are filled when absent.

    Returns:
        The record as written.
    """
    payload: dict[str, Any] = {"at": "2026-01-01T00:00:00Z", "why": "test fixture"}
    payload.update(record)
    path = journal_path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    return payload


def set_phase(ledger: Path, phase: int, status: str = "RUNNING") -> None:
    """Put a run in a phase, the way the state CLI does after a proved edge.

    Args:
        ledger: Task file.
        phase: Target phase, 1 to 5.
        status: Run status to record first, when it is not ``RUNNING``.
    """
    append(ledger, event="advance", why="test fixture", **{"from": 1, "to": phase})
    if status != "RUNNING":
        append(ledger, event="status", status=status)


def read(ledger: Path) -> list[dict[str, Any]]:
    """Read every journal record, oldest first.

    Args:
        ledger: Task file.

    Returns:
        Records in file order; empty when the journal does not exist.
    """
    path = journal_path(ledger)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
