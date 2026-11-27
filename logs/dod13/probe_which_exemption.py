"""Which exemption lets a phase-1 production write through inside the plugin tree?

Reproduced: identical payload, `allow` inside `skills/bug-fixer/logs/`, `deny` outside. This reads
the gate's own resolution — ledger, contract, project root, and which exemption matches the target
— so the cause is named instead of guessed.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO / "skills" / "bug-fixer"
GATE = PLUGIN / "hooks" / "bugfixer_gate.py"
TEST = PLUGIN / "tests" / "test_bugfixer_end_to_end_v3.py"


def load_gate() -> object:
    """Import the gate module by path.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location("gate_probe", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_probe"] = module
    spec.loader.exec_module(module)
    return module


def load_e2e() -> object:
    """Import the e2e helpers.

    Returns:
        The imported module.
    """
    sys.path.insert(0, str(PLUGIN / "tests"))
    spec = importlib.util.spec_from_file_location("e2e_probe2", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_probe2"] = module
    spec.loader.exec_module(module)
    return module


def inspect(gate: object, e2e: object, project: pathlib.Path) -> None:
    """Print what the gate resolves for a project.

    Args:
        gate: Imported gate module.
        e2e: Imported e2e module, for its fixtures.
        project: Scratch project root.
    """
    e2e._scratch(project)
    assert e2e._install(project).returncode == 0
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(project / e2e._PRODUCTION), "content": "x"},
        "cwd": str(project),
        "workspace_roots": [str(project)],
    }
    root = gate._project_root(event)
    contract = gate._find_contract(root)
    ledger = gate.discover_ledger(root)
    print(f"  project root : {root}")
    print(f"  contract     : {contract}")
    print(f"  ledger       : {ledger}")
    if ledger:
        text = ledger.read_text(encoding="utf-8")
        print(f"  phase        : {gate.read_phase(ledger)}  status: {gate.read_status(text)}")
    target = str(project / e2e._PRODUCTION)
    print(f"  is_plugin_file(target, root, dev=False): {gate._is_plugin_file(target, root, dev_plugin=False)}")
    print(f"  is_plugin_file(target, root, dev=True) : {gate._is_plugin_file(target, root, dev_plugin=True)}")
    parts = pathlib.Path(target).parts
    print(f"  matches skill-tree markers: {gate._matches_markers(parts, gate._SKILL_TREE_MARKERS)}")
    print(f"  matches wiring markers    : {gate._matches_markers(parts, gate._PLUGIN_WIRING_MARKERS)}")
    print(f"  is_doc_write              : {gate._is_doc_write(event['tool_input']['file_path'], ledger, root, 'write')}")
    print(f"  _is_code_write            : {gate._is_code_write(event['tool_input']['file_path'], ledger, 'write')}")


def main() -> int:
    """Inspect both locations.

    Returns:
        Exit code.
    """
    gate = load_gate()
    e2e = load_e2e()
    with tempfile.TemporaryDirectory() as outside:
        print("OUTSIDE the plugin tree:")
        inspect(gate, e2e, pathlib.Path(outside) / "proj")
    inside = PLUGIN / "logs" / "dod13"
    inside.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=inside) as box:
        print("\nINSIDE the plugin tree:")
        inspect(gate, e2e, pathlib.Path(box) / "proj")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
