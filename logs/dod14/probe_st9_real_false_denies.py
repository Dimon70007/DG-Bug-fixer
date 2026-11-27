"""How often does the gate call a read-only command a write?

Ground truth is `shell_scan.py`, an independent character-level parser, NOT the gate's regex —
otherwise the measurement would agree with the thing under test by construction.

Two counts are reported, because they answer different questions:

* **of all commands the agent ran** — how much of ordinary work is misclassified;
* **of all commands the gate would refuse as a write** — what share of refusals are wrong.

Only the second is a refusal rate: a false "write" classification is harmless unless the target
also fails another rule (outside project, phase ban). The probe reports the classification, and
says so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bugfixer_gate as gate  # noqa: E402
import shell_scan  # noqa: E402

TRANSCRIPTS = Path(
    "/Users/otvazhniy/.cursor/projects/Users-otvazhniy-github-my-linux-to-mac/agent-transcripts"
)


def _shell_commands(node) -> list[str]:
    out: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            tool = value.get("tool_name") or value.get("name") or value.get("tool")
            if isinstance(tool, str) and tool.lower() in gate._SHELL_TOOLS:
                payload = value.get("tool_input") or value.get("input") or value.get("args") or {}
                if isinstance(payload, dict):
                    command = payload.get("command")
                    if isinstance(command, str) and command.strip():
                        out.append(command)
                elif isinstance(payload, str) and payload.strip():
                    out.append(payload)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return out


def commands() -> list[str]:
    found: list[str] = []
    for path in sorted(TRANSCRIPTS.rglob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "bug-fixer" not in text and "TASK-086" not in text:
            continue
        for line in text.splitlines():
            if "Shell" not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for command in _shell_commands(record):
                if command not in found:
                    found.append(command)
    return found


def main() -> None:
    all_commands = commands()
    print(f"shell commands in this session's transcripts: {len(all_commands)}")
    if not all_commands:
        print("  nothing parsed — no claim can be made")
        return

    gate_write = [c for c in all_commands if gate._WRITE_COMMAND_RE.search(c)]
    false_write = [c for c in gate_write if not shell_scan.really_writes(c)]

    print(f"  gate classifies as a write        : {len(gate_write)}")
    print(f"    ...and it really writes         : {len(gate_write) - len(false_write)}")
    print(f"    ...and it does NOT write        : {len(false_write)}")
    if gate_write:
        print(f"  false-write share of classifications : {len(false_write) / len(gate_write) * 100:.1f}%")
    print(f"  false-write share of all commands    : {len(false_write) / len(all_commands) * 100:.1f}%")

    print("\n  what those 10 look like (redirection inside quotes):")
    for command in false_write[:10]:
        targets = shell_scan.redirection_targets(command)
        print(f"    targets={targets}  {command[:100]}")

    print("\n  control: three commands that DO write")
    for command in ("echo hi > out.txt", "cp a.py b.py", "git commit -m x"):
        print(f"    {command:26} really_writes={shell_scan.really_writes(command)}")


if __name__ == "__main__":
    main()
