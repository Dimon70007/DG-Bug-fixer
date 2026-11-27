"""ST-0 (partial): what the journal costs, what the ledger costs on re-read, and how the
estimate in TASK-086 compares with a real tokenizer.

The estimate in the task file used 2.5 bytes per token. Russian text tokenizes far worse than
that, so every "≈" number there is an OVERestimate. This probe replaces them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"

import tiktoken  # noqa: E402

ENCODER = tiktoken.get_encoding("cl100k_base")


def tokens(text: str) -> int:
    return len(ENCODER.encode(text, disallowed_special=()))


def main() -> None:
    print(f"tokenizer cl100k_base; bytes per token varies with the language:")
    for label, path in (
        ("SKILL.md (русский + код)", SKILL / "SKILL.md"),
        ("спека (русский)", REPO / "docs" / "bug-fixer-v3-spec.md"),
        ("TASK-085 (русский)", REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"),
    ):
        data = path.read_bytes()
        print(f"  {label:30} {len(data):>7} B / {tokens(data.decode('utf-8', 'replace')):>6} tok = {len(data) / tokens(data.decode('utf-8','replace')):.2f} B/tok")

    print("\n== journal: full file and per record ==")
    for name in (
        "TASK-085-bugfixer-standalone-plugin.md",
        "TASK-086-bugfixer-context-cost-research.md",
    ):
        path = REPO / "tasks" / (name + ".bugfixer.jsonl")
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        print(f"  {name[:40]:42} {len(records):3} записей, {tokens(text):5} токенов целиком")
        # The agent never reads the journal in full; the gate reads it in-process.
        print(f"    {'':42} гейт читает его как файл, в контекст агента он не попадает")

    print("\n== the estimate table in TASK-086 vs measurement ==")
    rows = [
        ("SKILL.md", 54066, 21600, tokens((SKILL / "SKILL.md").read_text(encoding="utf-8", errors="replace"))),
        ("README плагина", 40432, 16200, tokens((SKILL / "README.md").read_text(encoding="utf-8", errors="replace"))),
        ("спека", 137593, 55000, tokens((REPO / "docs" / "bug-fixer-v3-spec.md").read_text(encoding="utf-8", errors="replace"))),
        ("TASK-085 леджер", 221172, 88500, tokens((REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md").read_text(encoding="utf-8", errors="replace"))),
    ]
    print(f"  {'артефакт':18} {'байт':>8} {'оценка «≈»':>11} {'факт':>7} {'оценка / факт':>14}")
    for label, size, estimate, actual in rows:
        print(f"  {label:18} {size:>8} {estimate:>11} {actual:>7} {estimate / actual:>13.2f}x")

    print("\n== cost of the retyped reason text vs cost of the extra turn ==")
    journal = REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md.bugfixer.jsonl"
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    reason_tokens = sum(tokens(record["why"]) for record in records)
    ledger_tokens = tokens(
        (REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md").read_text(encoding="utf-8", errors="replace")
    )
    skill_tokens = tokens((SKILL / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    print(f"  все 117 причин отказа вместе        : {reason_tokens:6} токенов")
    print(f"  один SKILL.md                       : {skill_tokens:6} токенов")
    print(f"  леджер TASK-085, прочитанный целиком: {ledger_tokens:6} токенов")
    print()
    print(f"  -> причины отказов стоят {reason_tokens / skill_tokens:.2f} одного SKILL.md")
    print("  -> но каждый отказ = лишний ход модели, а ход заново отправляет весь контекст.")
    print(f"     один лишний ход при контексте TASK-085 ≈ {ledger_tokens} вх. токенов")
    print(f"     117 отказов × минимум один ход ≈ {117 * ledger_tokens / 1_000_000:.2f} млн вх. токенов")


if __name__ == "__main__":
    main()
