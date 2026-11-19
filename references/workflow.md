# Workflow details — hypotheses, discriminators, and what a real probe looks like

Reference for Phase 2. The phase text says *what* to do; this file shows *how* it
looks when it is done well, and how it looks when it only pretends to be done.

## Writing hypotheses that can die

A hypothesis you cannot refute is not a hypothesis, it is a belief. Each entry in
the ranked list needs all four parts:

| Part | Bad | Good |
|---|---|---|
| Cause | "something is wrong with quantisation" | "the fused path is selected for a non-contiguous input, so it reads the wrong stride" |
| Mechanism | "it corrupts data" | "the kernel walks `src1` as a flat array, so row `i` is read from `i*row_size` instead of `i*nb1`" |
| Prediction | "results will be different" | "the failure disappears when the input is made contiguous, and only for `k_v > k` cases" |
| Discriminator | "run the test" | "same shape, two layouts: contiguous `k_v == 0` vs view `k_v = 1600`. H1 predicts only the second fails" |

Two hypotheses minimum. If you can only produce one, the reason is usually that
you have not yet looked at the data from Phase 1 closely enough — say so in the
file rather than pretending the list is complete.

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

## Recording an experiment

See `templates/finding-block.md`. Two habits matter most:

- **Write the expectation before running.** If the expected values are written
  after the fact, the exercise degenerates into fitting a story to whatever
  happened.
- **Quote the fact.** Not "it passed" but the command and the line it printed.

## Anti-patterns seen in practice

- **Greening the sensor.** Making the test pass by loosening its tolerance or
  widening its assertion, while the product behaviour is unchanged. If the test
  has to change, the change needs its own justification recorded in the file.
- **Summing two effects.** Mixing codec cost and backend delta into one number
  hides which one moved. Measure them separately.
- **One sample as a verdict.** A single stochastic run is not a measurement. Pin
  the sampling (`temperature=0`, fixed seed) or repeat N times and report the
  spread.
- **Explaining away the counter-example.** If one arm disagrees with the theory,
  the theory is incomplete; record the arm rather than dropping it.
- **Fixing a sibling silently.** You noticed the same defect in three call sites;
  fix the one under test, note the other two in the file, and let the operator
  decide whether they are in scope.
- **"It works now"** with no named cause. If the cause is not confirmed, the bug
  is not fixed — it is dormant, and Phase 3 must say so.

## When a hypothesis is confirmed but the fix is too large

Stop and record it. A confirmed root cause with an out-of-scope fix is a valuable
finding and a legitimate `BLOCKED` outcome: the file then holds the cause, the
proof, the proposed fix, and the reason it was not applied here. What is not
acceptable is a partial fix that leaves the test red and the file silent.
