# Hypothesis block `### H<n>` — copy-paste template

Append one block per hypothesis to the **task file**. Numbers are sequential across
the whole file (`H0`, `H1`, …) and are never reused or reordered. `H0` is the one
obvious guess from phase 2; `H1…Hn` come from research in phase 3.

**One format.** `H0` and `H1…Hn` are parsed by the same reader. `H0` is not special
enough to earn a second format, and a second format drifts from the first.

```markdown
### H<n> — <one-line assertion: what exactly is wrong>

| Field | Value |
|---|---|
| Rank | <1..n, order of probing> |
| Status | derived (не редактируется) |
| Proposed | F<m> — the record that introduced this hypothesis |
| Origin | description / logs+search |
| Mechanism | <how the cause produces the symptom> |
| Prediction | <what we will observe if the cause is right> |
| Discriminator | <how it differs from the neighbouring hypothesis> |

#### Records
- F<m> → PROPOSED
```

## Field rules

| Field | Required | When absent |
|---|---|---|
| `Rank` | yes; `H0` is always `1` | the probe order is unknown, so step 4.0 cannot pick |
| `Status` | yes, but **derived** | computed from `#### Records`; an edited field is ignored |
| `Proposed` | yes; must point at an existing `### F<m>` | the hypothesis is not connected to the history |
| `Origin` | yes: `description` (2.2) or `logs+search` (3.5) | the source is unknown, so the report cannot say where it came from |
| `Mechanism` | yes | without it the hypothesis is not testable |
| `Prediction` | yes; merging is decided by it (3.6) | there is no way to tell whether two hypotheses differ |
| `Discriminator` | when there are two or more | the probe cannot separate this hypothesis from its neighbour |

## `#### Records` — the only source of truth for status

One bullet per verdict, oldest first. The last bullet wins.

```markdown
#### Records
- F4 → PROPOSED
- F5 → REFUTED
- F7 → PARTIALLY (A)
```

| Last bullet | Derived status |
|---|---|
| `→ PROPOSED` | `PROPOSED` |
| `→ CONFIRMED` | `CONFIRMED` |
| `→ REFUTED` | `REFUTED` |
| `→ PARTIALLY…` / `→ DEGRADED…` | **`PROPOSED`** — still in the queue, to be deepened |
| no bullets | `PROPOSED` |

A partial hit is **not** a status. The hypothesis is still unproven, so it stays in
the queue and `DEEPEN_SAME` deepens it. `Outcome` records what the probe achieved;
the hypothesis records whether it is settled.

## Deepening a hypothesis (`DEEPEN_SAME`)

Do **not** create a parallel hypothesis. Append to the same block:

1. rewrite `Mechanism`, `Prediction` or `Discriminator` with what the probe added;
2. add `Merge:` on the research pass (usually `Merge: none — same hypothesis`);
3. keep `Working on:` on the **same** `H<n>`;
4. continue with the new probe as `### F<m>` (`Kind | probe`).

Rewriting these three fields is allowed and expected. Rewriting `#### Records` is
also allowed — history is append-preferred, not append-only: a typo in your own note
must not be permanent.

## Minimal example

```markdown
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
```
