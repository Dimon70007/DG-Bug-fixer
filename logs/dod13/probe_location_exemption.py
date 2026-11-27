"""Why is a phase-1 production write allowed when the scratch project is inside the plugin tree?

The e2e proof harness put its scratch project under `logs/dod13/`, and every write there was allowed
— including ones the real test (which uses pytest's `tmp_path`, outside the tree) sees refused.
Either the gate has a defect that grants an exemption by location, or the harness was measuring
something else. This script asks the gate twice, with the same payload, from both locations.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO / "skills" / "bug-fixer"
TEST = PLUGIN / "tests" / "test_bugfixer_end_to_end_v3.py"


def load() -> object:
    """Import the e2e module for its fixtures.

    Returns:
        The imported module.
    """
    sys.path.insert(0, str(PLUGIN / "tests"))
    spec = importlib.util.spec_from_file_location("e2e_probe", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_probe"] = module
    spec.loader.exec_module(module)
    return module


def verdict(module: object, project: pathlib.Path) -> tuple[int, str]:
    """Ask the installed hook about a production write in phase 1.

    Args:
        module: The e2e module, for `_install` and `_write`.
        project: Scratch project root.

    Returns:
        Exit code and the refusal reason, if any.
    """
    module._scratch(project)  # type: ignore[attr-defined]
    installed = module._install(project)  # type: ignore[attr-defined]
    assert installed.returncode == 0, installed.stderr
    code, answer = module._write(project, module._PRODUCTION)  # type: ignore[attr-defined]
    return code, answer.get("agent_message", "")


def main() -> int:
    """Compare the verdict inside and outside the plugin tree."""
    module = load()
    with tempfile.TemporaryDirectory() as outside:
        code, reason = verdict(module, pathlib.Path(outside) / "proj")
        print(f"outside the plugin tree: exit={code} reason={reason[:80]!r}")

    inside = PLUGIN / "logs" / "dod13"
    inside.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=inside) as box:
        code, reason = verdict(module, pathlib.Path(box) / "proj")
        print(f"inside the plugin tree : exit={code} reason={reason[:80]!r}")

        project = pathlib.Path(box) / "proj"
        # Which ledger does the gate think it is enforcing, and which project root?
        contract = project / "bug-fixer.project.md"
        print(f"\ncontract present: {contract.is_file()}")
        print(f"task file       : {(project / 'tasks' / 'TASK-700.md').is_file()}")
        wrapper = project / ".cursor" / "skills" / "bug-fixer" / "hooks" / "run-gate.sh"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(project / module._PRODUCTION),  # type: ignore[attr-defined]
                "content": "x",
            },
            "cwd": str(project),
            "workspace_roots": [str(project)],
        }
        result = subprocess.run(
            [str(wrapper)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=str(project),
            check=False,
        )
        print(f"stderr: {result.stderr[:300]!r}")
        print(f"stdout: {result.stdout[:300]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
