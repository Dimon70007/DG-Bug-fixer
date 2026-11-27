"""Measure B-5 (project resolution) and B-7/B-8/B-10 instead of arguing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CORPUS = json.loads((SKILL / "tests/fixtures/cursor_payloads.json").read_text())


def measure_project_root() -> None:
    print("== B-5: _project_root per captured payload ==")
    for payload in CORPUS:
        event = {k: v for k, v in payload.items() if not k.startswith("_")}
        try:
            root = gate._project_root(event)
        except Exception as exc:  # noqa: BLE001
            print(f"  {payload['tool_name']:<18} RAISED {type(exc).__name__}: {exc}")
            continue
        print(f"  {payload['tool_name']:<18} root={root}")


def measure_contract_reach(tmp: Path) -> None:
    print("\n== B-5: how far up does the contract search reach? (depth limit 4) ==")
    home = tmp / "home"
    home.mkdir(parents=True)
    (home / "bug-fixer.project.md").write_text("Contract version: 1\n", encoding="utf-8")
    chain = home
    for i, name in enumerate(["a", "b", "c", "d", "e", "f"], start=1):
        chain = chain / name
        chain.mkdir()
        found = gate._find_contract(chain)
        print(f"  project {i} level(s) below the contract: {'found' if found else 'MISS'}")


def measure_no_contract(tmp: Path) -> None:
    print("\n== B-5: missing pieces ==")
    lonely = tmp / "lonely"
    lonely.mkdir()
    try:
        gate.discover_ledger(lonely)
        print("  no contract -> returned normally (SILENT ALLOW)")
    except Exception as exc:  # noqa: BLE001
        print(f"  no contract        -> {type(exc).__name__}")
    (lonely / "bug-fixer.project.md").write_text(
        "Contract version: 1\nContract verified by: x\n\n## Ledger\n\n"
        "Task/bug file: tasks/TASK-*.md\n",
        encoding="utf-8",
    )
    try:
        gate.discover_ledger(lonely)
        print("  contract, no file -> returned normally (SILENT ALLOW)")
    except Exception as exc:  # noqa: BLE001
        print(f"  contract, no file  -> {type(exc).__name__}")


def measure_interpreter() -> None:
    print("\n== B-10: run-gate.sh when python3 is absent from PATH ==")
    shim = Path(tempfile.mkdtemp()) / "shim"
    shim.mkdir()
    for tool in ("bash", "env", "dirname", "pwd", "cat"):
        for base in ("/bin", "/usr/bin"):
            candidate = Path(base) / tool
            if candidate.exists():
                (shim / tool).symlink_to(candidate)
                break
    env = dict(os.environ)
    env["PATH"] = str(shim)
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x.py"}, "cwd": "."})
    proc = subprocess.run(
        [str(SKILL / "hooks/run-gate.sh")],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(SKILL),
    )
    print(f"  bash present, no python3 -> exit={proc.returncode} stderr={proc.stderr.strip()[:80]!r}")
    print(f"  failClosed:true turns this into: {'BLOCK every tool call' if proc.returncode else 'allow'}")

    print("\n== B-10: a python3 that is present but fails ==")
    fake = shim / "python3"
    fake.write_text('#!/bin/bash\necho "python3: broken install" >&2\nexit 1\n', encoding="utf-8")
    fake.chmod(0o755)
    proc = subprocess.run(
        [str(SKILL / "hooks/run-gate.sh")],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(SKILL),
    )
    print(f"  exit={proc.returncode} stderr={proc.stderr.strip()[:80]!r}")


def measure_version_and_packaging() -> None:
    print("\n== B-7: where the version lives ==")
    for rel in (".cursor-plugin/plugin.json", "SKILL.md", "pyproject.toml", "README.md", "CHANGELOG.md"):
        p = SKILL / rel
        print(f"  {rel:<28} {'present' if p.is_file() else 'ABSENT'}")
    print(f"  parent CHANGELOG: {(SKILL.parent.parent / 'CHANGELOG.md').is_file()}")

    print("\n== B-10: interpreter version checks in shipped code ==")
    hits = []
    candidates = sorted((SKILL / "hooks").glob("*.py"))
    candidates += [p for p in (SKILL / "bin").iterdir() if p.is_file()]
    candidates.append(SKILL / "install.py")
    for path in candidates:
        if path.suffix not in {".py", ""}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("version_info", "sys.version", "MIN_PYTHON"):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    print(f"  {hits or 'none — python3 is whatever PATH resolves'}")
    rp = [l.strip() for l in (SKILL / "pyproject.toml").read_text().splitlines() if "requires-python" in l]
    print(f"  pyproject: {rp}")

    print("\n== B-8: who references the installer ==")
    out = subprocess.run(
        [
            "rg",
            "-l",
            "-e",
            "install\\.py",
            "-e",
            "install-ai-skill",
            "--glob",
            "!logs/**",
            "--glob",
            "!__pycache__",
            "--glob",
            "!tests/__pycache__/**",
            str(SKILL),
        ],
        text=True,
        capture_output=True,
    )
    for line in sorted(out.stdout.split()):
        print(f"  {line.replace(str(SKILL) + '/', '')}")

    print("\n== B-7: parent references to module subpaths ==")
    parent = SKILL.parent.parent
    out = subprocess.run(
        [
            "rg",
            "-n",
            "-e",
            r"skills/bug-fixer/(hooks|runner|bin)",
            "-e",
            "install-ai-skill",
            "--glob",
            "!skills/bug-fixer/**",
            "--glob",
            "!logs/**",
            str(parent),
        ],
        text=True,
        capture_output=True,
    )
    lines = out.stdout.strip().splitlines()
    print(f"  {len(lines)} reference(s)")
    for line in lines[:14]:
        print(f"  {line.replace(str(parent) + '/', '')}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        measure_project_root()
        measure_contract_reach(tmp)
        measure_no_contract(tmp)
    measure_interpreter()
    measure_version_and_packaging()


if __name__ == "__main__":
    main()
