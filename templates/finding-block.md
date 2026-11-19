# Finding block `F<n>` — copy-paste template

Append one block per finding to the **target task file**. Numbers are sequential
across the whole file (`F1`, `F2`, ...) and are never reused or reordered.

```markdown
### F<n> — <one-line claim, stated as a fact once proven>

| Field | Value |
|---|---|
| Iteration | `N` = <phase 1->2->3 pass number> |
| Experiment | `M` = <experiment number inside this pass> |
| Commit / tree | `<sha>` or `<sha> + uncommitted` |
| Command | `<exact command>` |

**Hypothesis.** <the claim this experiment tests, and any rival it must be
distinguished from>

**Expected.** If H: <observation>. If not-H: <different observation>.

**Fact.** <exact output, exit code, or the decisive log lines. Trim to the lines
that carry the signal; put the full dump in `logs/<task>-<stamp>-knowledge/` and
link it here.>

**Verdict.** H CONFIRMED / H REFUTED — <one sentence why the fact supports it>.

**Consequence.** <what changes for the next experiment, or what the fix must do.>
```

## Rules for the block

- **The fact is quoted, not paraphrased.** A paraphrase drifts on re-read; a quote
  does not. Keep the numbers and units exactly as printed.
- **One claim per block.** If an experiment settles two independent questions,
  write two blocks.
- **Refutations are findings too.** A refuted hypothesis stays in the file; it is
  the reason the next reader does not repeat the experiment.
- **No block without an experiment.** "I think it is X" belongs in the hypothesis
  field of the block that tests it, not in a block of its own.
- **`N` and `M` are mandatory.** They are how the loop guard and the reviewer tell
  a fresh iteration from a half-hypothesis inside the same one.

## Minimal example

```markdown
### F3 — The gate is skipped, not failing

| Field | Value |
|---|---|
| Iteration | `N` = 2 |
| Experiment | `M` = 1 |
| Commit / tree | `abc1234` |
| Command | `./bin/test-op -o MUL_MAT -p "k_v=1600"` |

**Hypothesis.** The kernel returns wrong values (H1), or the fused path is never
selected so the correct fallback runs and the assertion trips elsewhere (H2).

**Expected.** If H1: numeric mismatch `ERR > 0.0005` on a computed result. If H2:
the case reports `not supported` or prints no `ERR` line at all.

**Fact.** `ERR = 0.07946 > 0.0005 ... k_v=1600` — a numeric comparison ran and
failed. With `TQ_NO_ROTATE=1` the same two cases print `2/2 tests passed`.

**Verdict.** H1 CONFIRMED for the numeric path; H2 REFUTED, the fused path runs.

**Consequence.** The fix belongs in the fused path selection, not in the
comparison harness. Next: test whether the input layout is the trigger.
```
