"""ST-0 (partial): real token counts with tiktoken, not bytes/2.5.

Counts the artifacts that enter the agent's context, plus what the denials in the two real
journals cost. Tokenizer is named so the numbers can be reproduced; a different tokenizer
gives different absolute values but the same ordering.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"

import tiktoken  # noqa: E402


def count(encoder, text: str) -> int:
    return len(encoder.encode(text, disallowed_special=()))


def main() -> None:
    encoder = tiktoken.get_encoding("cl100k_base")
    print(f"tokenizer: cl100k_base (tiktoken {tiktoken.__version__})")
    print("absolute values differ per model; ratios and ordering do not\n")

    files = [
        ("SKILL.md (грузится каждый вызов скилла)", SKILL / "SKILL.md"),
        ("README.md плагина", SKILL / "README.md"),
        ("спека (норма)", REPO / "docs" / "bug-fixer-v3-spec.md"),
        ("моя задача TASK-086", REPO / "tasks" / "TASK-086-bugfixer-context-cost-research.md"),
        ("задача TASK-085", REPO / "tasks" / "TASK-085-bugfixer-standalone-plugin.md"),
    ]
    print(f"{'артефакт':44} {'байт':>8} {'токенов':>9}")
    for label, path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        print(f"{label:44} {len(text.encode()):>8} {count(encoder, text):>9}")

    references = sorted((SKILL / "references").glob("*.md"))
    total = sum(count(encoder, p.read_text(encoding="utf-8", errors="replace")) for p in references)
    print(f"{'references/*.md вместе (' + str(len(references)) + ' файла)':44} {'':>8} {total:>9}")

    print("\n== цена отказов в журналах ==")
    for name in (
        "TASK-085-bugfixer-standalone-plugin.md",
        "TASK-086-bugfixer-context-cost-research.md",
    ):
        journal = REPO / "tasks" / (name + ".bugfixer.jsonl")
        if not journal.is_file():
            continue
        records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
        reasons = [record["why"] for record in records]
        tokens = sum(count(encoder, reason) for reason in reasons)
        print(f"  {name[:44]:46} {len(records):3} отказов, {tokens:6} токенов текста причин")
        if records:
            print(f"    {'':46} в среднем {tokens // len(records)} токенов на отказ")

    print("\n== что стоит один повтор ==")
    example_reason = "Phase 1 forbids production-code changes: only Phase 4 (probe) may edit code, " \
        "and only as a probe of a named hypothesis. Phases 1-3 collect evidence; Phase 5 judges it."
    print(f"  типичная причина отказа            : {count(encoder, example_reason):3} токенов")
    print("  плюс ответ агента: перечитать причину, придумать обход, заново вызвать инструмент")
    print("  -> стоимость повтора задаётся не текстом причины, а целым лишним шагом модели")


if __name__ == "__main__":
    main()
