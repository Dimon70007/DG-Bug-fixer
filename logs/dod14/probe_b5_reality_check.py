"""Did I measure a payload Cursor actually sends, or one I invented?

The corpus (tests/fixtures/cursor_payloads.json, captured from a live Cursor 3.32.10 hook) is the
authority. For Shell it records: top-level `cwd` = '' and `workspace_roots` = [project].

If that is the real shape, `_project_root` never sees a subdirectory, `workspace_roots` is used,
and the "deep cwd" scenario I reported cannot happen. Measure it instead of asserting it.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CONTRACT = (
    "Contract version: 1\n"
    "Contract verified by: agent 2026-11-27 — pytest ran\n\n"
    "## Ledger\n\n- Task/bug file: tasks/TASK-*.md\n"
    "## Tests\n\nFull suite: pytest\nTest paths: tests/\n"
    "## Search\n\nSearch: none\n"
    "## Extra prohibitions\n\n- no sudo\n"
)

CORPUS = json.loads((SKILL / "tests/fixtures/cursor_payloads.json").read_text())


def rel(path: Path, base: Path) -> str:
    return str(path.resolve()).replace(str(base.resolve()) + "/", "")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        project = tmp / "myproject"
        (project / "tasks").mkdir(parents=True)
        (project / "bug-fixer.project.md").write_text(CONTRACT, encoding="utf-8")
        (project / "tasks" / "TASK-1.md").write_text("STATUS: RUNNING\n", encoding="utf-8")

        # A deep tree, so a subdirectory cwd would be visible if one ever arrived.
        deep = project / "src" / "pkg" / "sub" / "inner" / "leaf"
        deep.mkdir(parents=True)

        print("== replay every captured payload against a real project tree ==")
        for row in CORPUS:
            event = {k: v for k, v in row.items() if not k.startswith("_")}
            # Point the recorded /project at this temp project.
            event = json.loads(json.dumps(event).replace("/project", str(project)))
            tool = event.get("tool_name")
            cwd = event.get("cwd")
            root = gate._project_root(event)
            try:
                ledger = gate.discover_ledger(root)
                outcome = f"OK {rel(ledger, tmp)}"
            except Exception as exc:  # noqa: BLE001
                outcome = f"{type(exc).__name__}"
            label = f"cwd={cwd!r}" if cwd else "cwd=none/empty"
            print(f"  {tool:<18} {label:<28} root={rel(root, tmp) if root.is_relative_to(tmp) else root}")
            print(f"  {'':<18} -> {outcome}")

        print("\n== the payload I invented for the 'defect' ==")
        invented = {
            "tool_name": "Shell",
            "tool_input": {"command": "pytest -q", "cwd": str(deep)},
            "cwd": str(deep),
            "workspace_roots": [str(project)],
        }
        root = gate._project_root(invented)
        print(f"  top-level cwd = a subdirectory -> root={rel(root, tmp)}")
        print("  In the corpus, no payload has a non-empty top-level cwd except Read,")
        print("  and there it is the project root itself.")


if __name__ == "__main__":
    main()
