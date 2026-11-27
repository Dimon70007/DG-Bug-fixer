# Finding block `### F<n>` — copy-paste template

Append one block per cycle to the **task file**. Numbers are sequential across the
whole file (`F1`, `F2`, …) and are never reused or reordered.

`Kind` is what makes a record readable without guessing: `route` from phase 2,
`research` from phase 3, `probe` from phase 4. Only a `probe` can close the move to
phase 5, so a routing note cannot accidentally pass for a proof.

```markdown
### F<n> — <what was probed and how it ended>

| Field | Value |
|---|---|
| Kind | route / research / probe |
| Scope | task / H<n> |
| Responds to | F<m> / — |
| Supersedes | F<m> / — |
| Iteration | <pass number> |
| Outcome | <ROUTED / RESEARCH: none / CONFIRMED / REFUTED / PARTIALLY (A|B) / DEGRADED (C)> |
```

## What goes in the body, per `Kind`

| `Kind` | Written by | Body carries |
|---|---|---|
| `route` | 2.2 | `Outcome | ROUTED` — and nothing else: phase 2 makes one guess and does not probe |
| `research` | 3.5 | `Merge:` note, log destinations, links found, `Outcome | RESEARCH: …` |
| `probe` | 4.9 | the markers of one probe cycle, below |

### A probe block, in full

```markdown
### F<n> — Проба H<k>: <заголовок>

| Field | Value |
|---|---|
| Kind | probe |
| Scope | H<k> |
| Responds to | F<m> |
| Supersedes | — |
| Iteration | <pass number> |
| Outcome | CONFIRMED |

| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| `test_a` | <behaviour> | `stale_hits` | 7 | 0 | 0 | исправлено |

FAILING TEST: pytest tests/test_a.py -> failed
FAILING TEST: pytest tests/test_b.py -> failed
Corner cases: none — <why this behaviour has no edges>

ФАКТ (RED): pytest tests/test_a.py tests/test_b.py -> 2 failed
Recheck H<k> #1 — <what was wrong with the test itself>
ФАКТ (логи): <the decisive log lines, quoted>
Bug: stable
Attempt <k> consumed
Secrets scan: clean
ФАКТ (GREEN): pytest -> 1654 passed
```

## Required markers by verdict

The gate reads these; a missing one names itself in the refusal.

| Verdict | Markers |
|---|---|
| `CONFIRMED` → `PROVEN` | `ФАКТ (RED):` · `ФАКТ (GREEN):` · `ФАКТ (логи):` · `Bug:` · `Attempt <k> consumed` · `Secrets scan:` · results table · `Kind: probe` · ≥2 `FAILING TEST:` or `Corner cases: none` |
| `REFUTED` → `NEXT_IN_QUEUE` / `QUEUE_EXHAUSTED` | `ФАКТ (логи):` · `Bug:` · `Revert: <files>` (`NEW INFORMATION:` for the second edge) |
| `PARTIALLY` → `DEEPEN_SAME` | `ФАКТ (дельта):` with three numbers, **or** `ФАКТ (регрессия): <test>`; `Revert:` forbidden with a delta, mandatory without one; `Resolution:` when the metrics disagree |

## Field rules

- **`Scope` names the hypothesis.** `task` is for baseline and research records;
  a probe without a hypothesis is an edit nobody can attribute.
- **`Responds to`** chains the records. `F7` responding to `F4` is how a later
  reader sees that a refutation followed a partial hit.
- **`Supersedes`** is used when a record corrects an earlier one. It is a pointer,
  not a reason to delete the old block: the old block is why the new one exists.
- **`Outcome` carries the verdict**, and it is the only place the verdict lives.
  `H<n>` has no `Outcome` field.
- **The results table is a table on purpose.** Its columns (`До`, `После`,
  `Порог`, `Вывод`) are the contract; a table without them is not a measurement.

## Rules that survive review

- **The fact is quoted, not paraphrased.** A paraphrase drifts on re-read; a quote
  does not. Keep numbers and units exactly as printed.
- **One cycle per block.** Two independent verdicts are two blocks.
- **Refutations are findings.** A refuted hypothesis stays in the file; it is the
  reason the next reader does not repeat the probe.
- **No block without a probe.** "I think it is X" belongs in `Mechanism`, not in a
  record of its own.
- **`Attempt` only after the code change.** The marker is how `A` counts work that
  reached production code; writing it for a test edit inflates the cap.

## Minimal example

```markdown
### F5 — Проба H1: механизм не тот

| Field | Value |
|---|---|
| Kind | probe |
| Scope | H1 |
| Responds to | F4 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | REFUTED |

ФАКТ (логи): invalidate вызывается на всех четырёх ветках — механизм H1 отсутствует
Bug: stable
Revert: src/cache.py
NEW INFORMATION: инвалидация работает, дубли приходят из второго вызывающего
```
