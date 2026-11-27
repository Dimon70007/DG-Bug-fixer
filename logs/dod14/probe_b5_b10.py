"""Measure B-5 (project resolution) and B-10 (hook interpreter) instead of arguing."""

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
        print(f"  {payload['tool_name']:<18} root={root}  (process cwd={Path.cwd()})")


def measure_contract_reach(tmp: Path) -> None:
    print("\n== B-5: does a home-level contract reach a project 5 levels down? ==")
    home = tmp / "home"
    deep = home / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (home / "bug-fixer.project.md").write_text("Contract version: 1\n", encoding="utf-8")
    found = gate._find_contract(deep)
    print(f"  contract at {home}, search from {deep}")
    print(f"  _find_contract -> {found}")
    for depth in range(1, 7):
        probe = home.joinpath(*["a", "b", "c", "d", "e"][:depth])
        r = gate._find_contract(probe)
        print(f"  depth {depth}: {'found' if r else 'MISS'}")


def measure_no_contract(tmp: Path) -> None:
    print("\n== B-5: no contract anywhere -> what does discover_ledger raise? ==")
    lonely = tmp / "lonely"
    lonely.mkdir()
    try:
        gate.discover_ledger(lonely)
        print("  returned normally (silent allow!)")
    except Exception as exc:  # noqa: BLE001
        print(f"  {type(exc).__name__}: {str(exc)[:110]}")


def measure_interpreter() -> None:
    print("\n== B-10: run-gate.sh with no python3 on PATH ==")
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    # /usr/bin/python3 exists on macOS, so build a PATH without any python at all.
    empty = Path(tempfile.mkdtemp()) / "emptybin"
    empty.mkdir()
    env["PATH"] = str(empty)
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x.py"}, "cwd": "."})
    proc = subprocess.run(
        [str(SKILL / "hooks/run-gate.sh")],
        input=payload, text=True, capture_output=True, env=env, cwd=str(SKILL),
    )
    print(f"  exit={proc.returncode} stderr={proc.stderr.strip()[:120]!r}")
    print(f"  failClosed in .cursor/hooks.json would turn this into: {'BLOCK' if proc.returncode else 'allow'}")


def measure_version_guard() -> None:
    print("\n== B-10: is any interpreter version checked? ==")
    hits = []
    for path in list((SKILL / "hooks").glob("*.py")) + list((SKILL / "bin").iterdir()) + [SKILL / "install.py"]:
        if path.suffix not in {".py", ""}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("version_info", "sys.version", "MIN_PYTHON", "requires-python"):
            if needle in text:
                hits.append(f"{path.name}:{needle}")
    print(f"  {hits or 'none — python3 comes from PATH unversioned'}")
    print(f"  pyproject requires-python = {[l for l in (SKILL/'pyproject.toml').read_text().splitlines() if 'requires-python' in l]}")

    print("\n== B-7: where does the version live? ==")
    for rel in (".cursor-plugin/plugin.json", "SKILL.md", "pyproject.toml", "CHANGELOG.md"):
        p = SKILL / rel
        print(f"  {rel:<28} {'present' if p.is_file() else 'ABSENT'}")

    print("\n== B-8: who needs install.py? ==")
    out = subprocess.run(["rg", "-l", "install\\.py|install\\.sh", "--glob", "!logs/**", str(SKILL)],
                         text=True, capture_output=True)
    for line in sorted(out.stdout.split()):
        print(f"  {line.replace(str(SKILL) + '/', '')}")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        measure_project_root()
        measure_contract_reach(tmp)
        measure_no_contract(tmp)
    measure_interpreter()
    measure_version_guard()


if __name__ == "__main__":
    main()
