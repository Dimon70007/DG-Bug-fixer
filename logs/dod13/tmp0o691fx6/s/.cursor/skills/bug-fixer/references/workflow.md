# Workflow details — hypotheses, discriminators, and what a real probe looks like

Reference for phases 3 and 4. The phase text says *what* to do; this file shows
*how* it looks when it is done well, and how it looks when it only pretends to be
done.

## Writing hypotheses that can die

A hypothesis you cannot refute is not a hypothesis, it is a belief. Each entry in
the ranked list needs all four parts:

| Part | Bad | Good |
|---|---|---|
| Cause | "something is wrong with quantisation" | "the fused path is selected for a non-contiguous input, so it reads the wrong stride" |
| Mechanism | "it corrupts data" | "the kernel walks `src1` as a flat array, so row `i` is read from `i*row_size` instead of `i*nb1`" |
| Prediction | "results will be different" | "the failure disappears when the input is made contiguous, and only for `k_v > k` cases" |
| Discriminator | "run the test" | "same shape, two layouts: contiguous `k_v == 0` vs view `k_v = 1600`. H1 predicts only the second fails" |

**The cheap guess comes first, and it is one guess.** Phase 2 (`route`) writes
exactly one `### H0` from the description alone. Research in phase 3 adds
`H1…Hn` — one or more, and at least one must still be unprobed before phase 4
opens. A single hypothesis in the whole task means research added nothing.

## The discriminator test

The point of an experiment is to make two hypotheses predict **different**
observations. If both predict "it fails", the experiment tells you nothing and
the run is wasted.

Worked pattern from this repo's own investigations:

| | Hypothesis A (fused path) | Hypothesis B (harness bug) |
|---|---|---|
| Predicts with a known-good control | passes | fails |
| Predicts on the suspect path | fails | fails everywhere |
| Discriminating observation | the control passes | the control also fails |

Hypothesis B was refuted by the control passing; hypothesis A survived. Note the
control is what did the work — "the suspect fails" was compatible with both.

### Cheap discriminators to reach for first

1. **Env-knob bisect.** Many backends expose a switch that disables one
   optimisation (`TQ_NO_ROTATE=1`, `GGML_TURBO_MMA_FUSED=0`, ...). Flipping it
   isolates the branch without a rebuild.
2. **Layout / shape twins.** Same data, two layouts (contiguous vs view), or two
   shapes either side of a threshold. If only one fails, the branch selection is
   the subject.
3. **Old-vs-new pin.** If a commit range is suspect, run the same probe on the
   older pin. Equal results exonerate the commits; different results implicate
   them, and the range is then small enough to bisect.
4. **Backend twin.** Same op on CPU vs GPU. A backend-specific difference is a
   kernel defect; an identical difference on both is not.
5. **`0` vs `N` for an optional feature.** Turning a feature off is often the
   cheapest way to attribute a difference to it.

What all five have in common: they change **one** thing and they have an
**expected outcome under each hypothesis** written down *before* the run. A probe
whose expectation is written after seeing the output is not evidence.

## The results table: "changed" is not "better"

Every probe carries a table whose columns are the contract — a table without
`До`, `После`, `Порог`, `Вывод` is not a measurement, and the gate will not open
the move to phase 5 without it.

### Example 1 — fixed

```markdown
| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| `test_cache_ttl` | кеш не отдаёт устаревшее после перезаписи | `stale_hits` | 7 | 0 | 0 | исправлено |
```

The number reached the threshold, and the verdict is `CONFIRMED`. A reader can
check this line without running anything.

### Example 2 — unchanged, and that is the honest answer

```markdown
| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| `test_p95_latency` | ответ укладывается в бюджет | `p95_ms` | 120 | 118 | ≤130 | без изменений |
```

**This second example matters more than the first.** `120 → 118` looks like
progress and is not: the threshold was already met before the probe, so the probe
proved nothing about the hypothesis. Writing "без изменений" here is what stops
the next reader from treating a 2 ms wobble as evidence.

### Example 3 — improved here, broken there

```markdown
| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| `test_no_duplicates` | дублей нет | `dup_count` | 7 | 2 | 0 | лучше, порог не достигнут |
| `test_orders_sorted` | порядок сохранён | `pass` | 1 | 0 | 1 | регрессия |
```

Two measurements moved in opposite directions. That is a choice, not a partial
success, and the choice has to be named:

```markdown
Resolution: latency важнее дублей — задача про p95, дубли вторичны.
duplicates 7 → 2 (лучше), p95_ms 120 → 150 (хуже, порог ≤130). Принимаю: p95 не уложился,
правка требует доработки; вердикт PARTIALLY CONFIRMED, случай B.
```

The gate checks that `Resolution:` exists. Whether the criterion is *good* is read
by a human in the report: **не автоматизируется** — это названо, а не спрятано.

## Recording a probe

See `templates/finding-block.md`. Three habits matter most:

- **Write the expectation before running.** If the expected values are written
  after the fact, the exercise degenerates into fitting a story to whatever
  happened.
- **Quote the fact.** Not "it passed" but the command and the line it printed.
- **Do not skip the logs.** `ФАКТ (логи):` is required even when the fix works:
  without it the record says the change helped but not why, and the next defect of
  the same class repeats.

## A worked ledger fragment

This block is parsed by the same readers the gate uses, so it can be copied as a
starting point and will not teach a format the gate cannot see.

```markdown
Working on: H2

### H2 — Второй вызывающий сбрасывает чужой ключ

| Field | Value |
|---|---|
| Rank | 3 |
| Status | derived (не редактируется) |
| Proposed | F4 |
| Origin | logs+search |
| Mechanism | caller B calls invalidate() with its own key, and the shared cache resets key A |
| Prediction | isolating the key namespace removes both the stale read and the duplicates |
| Discriminator | against H1: fixes both symptoms, not only staleness |

#### Records
- F4 → PROPOSED
- F6 → CONFIRMED

### F6 — Проба H2: изоляция ключей

| Field | Value |
|---|---|
| Kind | probe |
| Scope | H2 |
| Responds to | F5 |
| Supersedes | — |
| Iteration | 1 |
| Outcome | CONFIRMED |

| Тест | Что проверяет | Метрика | До | После | Порог | Вывод |
|---|---|---|---|---|---|---|
| `test_cache_ttl` | stale после перезаписи | `stale_hits` | 7 | 0 | 0 | исправлено |

FAILING TEST: pytest tests/test_cache_ttl.py -> failed
ФАКТ (RED): pytest tests/test_cache_ttl.py -> 1 failed
ФАКТ (логи): caller B больше не трогает ключ A
Bug: stable
Attempt 3 consumed
Secrets scan: clean
ФАКТ (GREEN): pytest -> 1654 passed
Corner cases: none — два целевых покрывают оба симптома
```

## Anti-patterns seen in practice

- **Greening the sensor.** Making the test pass by loosening its tolerance or
  widening its assertion, while the product behaviour is unchanged. If the test
  has to change, the change needs its own justification recorded in the file.
- **Summing two effects.** Mixing codec cost and backend delta into one number
  hides which one moved. Measure them separately.
- **One sample as a verdict.** A single stochastic run is not a measurement. Pin
  the sampling (`temperature=0`, fixed seed) or repeat until the answer is stable
  — three runs is the practical floor for a flaky bug.
- **Explaining away the counter-example.** If one arm disagrees with the theory,
  the theory is incomplete; record the arm rather than dropping it.
- **Fixing a sibling silently.** You noticed the same defect in three call sites;
  fix the one under test, note the other two in the file, and let the operator
  decide whether they are in scope.
- **"It works now"** with no named cause. If the cause is not confirmed, the bug
  is not fixed — it is dormant, and the verdict must say `REFUTED`, not
  `CONFIRMED`.
- **Refuting with the wrong test.** The probe's own test was broken, so it stayed
  green; the hypothesis is not refuted, the instrument is. That is a `Recheck`
  (`4.4`), not a verdict (`4.8`).
- **Deepening by adding a hypothesis.** A partial hit is deepened in the *same*
  `### H<n>` — `Mechanism`, `Prediction`, `Discriminator` get rewritten. A parallel
  hypothesis splits the evidence and neither one is ever settled.

## When a hypothesis is confirmed but the fix is too large

Stop and record it. A confirmed root cause with an out-of-scope fix is a valuable
finding and a legitimate `BLOCKED` outcome with `Причина: operator-needed`: the
file then holds the cause, the proof, the proposed fix, and the reason it was not
applied here. What is not acceptable is a partial fix that leaves the test red and
the file silent.
