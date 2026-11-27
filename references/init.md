# Phase 0a — initialise the project contract

`bugfixer init` creates the file. It can read a filesystem, so it fills in what a filesystem
proves: the ledger directory, the test directory, a dump directory, and the test command from a
manifest. It **cannot** see what a running agent can see — the tools this session actually
exposes, whether the test command works, which search servers answer.

That part is yours. This step takes about a minute and it is the difference between a contract
the gate enforces and a contract that quietly lies.

## When to run it

- Phase 0, step 0.1: no `bugfixer.project.md` in the project root.
- Or `bugfixer doctor` exits `2` and names a field.

If the file already exists and is complete, skip this and go to 0.2.

## Step 1 — generate

```
bugfixer init --project .
```

Read the output. It names the fields it could not settle. Do **not** hand-write the file from
scratch: `init` writes it in the shape the validator reads, and a hand-written contract that
drifts from that shape is the defect this step exists to prevent.

The generated file carries two lines about its own trustworthiness:

```
Contract version: 1
Contract verified by: not yet
```

`not yet` is the important one. `init` read your filesystem; it did not run your test command or
call a search tool. **The gate refuses work while that line says `not yet`** — that is the
difference between a contract that describes a project and one that describes what someone
believed about it.

## Step 2 — confirm each field against *your* runtime

This is the part only you can do. For every field below, check the value and correct it.

### `Full suite:` — run it

```bash
<the command in the contract>
```

- It must exist, and it must pass on a clean tree.
- If it fails, the contract is wrong: fix the command, not the tests.
- If the project has no tests, write `Full suite: none` and record why in `## Prohibitions` —
  a bug-fixer run without a suite has nothing to compare against, and Phase 1 will say so.

Never write a command you have not run. A command that fails to start reads as a broken build
and wastes the first iteration.

### `Search:` — name the tools you actually have

Look at what this session exposes, not at what is popular:

- MCP servers: the tool names you can call right now (they appear with the server's prefix).
- A builtin web search, if the editor provides one.
- If nothing is available: `Search: none`. That is a normal answer — step 3.4 skips research and
  hypotheses are built from Phase 1 evidence. It is **not** a reason to skip the phase.

Then replace the list in the contract with those names. Keep the names as the session shows
them: a name the agent cannot call is worse than an empty field, because step 3.3 would try it
and fail.

**Never copy a token, key or URL with credentials from an MCP config into this file.** The
contract is committed. `init` already reads only server names for this reason; if you are typing
in this file, keep the same rule.

### `Task/bug file:` — check the pattern matches

```bash
ls <the pattern>
```

- It must match at least one file, or the gate cannot find the ledger and will refuse work.
- If the project keeps tasks elsewhere, name that place. `tasks/TASK-*.md`, `docs/bugs/*.md`,
  `issues/` are all fine — the pattern is what matters.
- If several files match and they are equally new, the gate refuses rather than guess which bug
  is being fixed. Touch the one you are working on.

### `Test paths:` — the prefixes that are tests

Prefixes from the project root, comma-separated: `tests/, src/pkg/tests/`. This is what lets the
gate allow a test edit in phases 1–3 while refusing a production edit, so it has to be right: a
missing prefix means the agent cannot write the test Phase 1 requires.

### `Prohibitions:` — keep the defaults, add yours

The generated file already carries the spec's §15 rules (no `sudo`, no destructive commands, no
secrets in queries, debug logging removed before the final commit). Add what this project
forbids: public API changes, database schema edits, snapshot rewrites, dependency changes.

Write "not allowed" when something is not allowed. An empty field is not an answer.

### `Raw dumps:` — optional

A directory for logs too large for the task file. Absent is legal: the skill keeps that material
in the task file instead. `bugfixer doctor` warns and exits `0`.

## Step 3 — verify

```
bugfixer doctor --project .
```

Exit `0` means the gate can use the contract. Exit `2` names the field that is still wrong.

Then **confirm the contract**, which is what opens the gate:

```
Contract verified by: agent 2026-11-27 — pytest ran, 12 passed; tavily_search present
```

Record what you actually did, not a bare name: the next reader needs to know whether the command
was run or merely read. If you could not confirm something, say that instead —
`Contract verified by: none — no test command could be run (no toolchain on PATH)`. An explicit
refusal is an answer; silence is not.

Then prove the gate is live, not merely configured:

```bash
# A production edit in phase 1 must be refused.
bugfixer gate < /dev/null || true
```

If that is allowed while a phase-1 run is active, the contract is being read but the ledger is
not — run `doctor` again and read the `ledger` line it prints.

## Step 4 — record what you changed

If you corrected a field, say so in the task file where the run is recorded:

```
Contract: Full suite corrected to `poetry run pytest -q` (the generated `pytest` used the system interpreter)
```

A contract edit is a decision; a decision nobody wrote down is re-litigated next month.

## When the contract is out of date

`doctor` warns when `Contract version:` does not match the plugin's schema. That is not cosmetic:
a field added to the schema after the file was written is simply absent, and the field checks
cannot tell "absent because the project has none" from "absent because nobody asked yet".

Do not hand-edit the version number. Regenerate and carry the values over:

1. `bugfixer init --project . --force` writes a fresh contract over the old one — so read the old
   file first, or copy it somewhere safe (`cp bug-fixer.project.md /tmp/contract.old`).
2. Copy your project's answers from the old file into the new one. The generated values are
   starting points; the ones you and the agent confirmed are decisions.
3. Walk steps 2 and 3 again for anything that changed — especially `Full suite:`, because the
   agent must have run it.
4. Record the migration in the task file, with the version numbers:

```
Contract: regenerated for schema v1 (was v0); Full suite and Search re-confirmed
```

If the plugin cannot be updated to match the contract — for example a teammate is on a newer
schema — say so rather than editing the number. A version that lies is worse than a warning.

## If this project has no test suite

`Full suite: none` is a refusal, not a mode. Phase 1 cannot record a baseline and Phase 4 cannot
compare before and after (`§1`, `§6` point 2), so the run would end in `BLOCKED` after you had
already written a failing test. Stop, tell the user, and offer three options, best first:

1. **Add a suite.** Propose three concrete options for this project — framework, the first test
   worth writing, and the exact command — then let the user choose. A run needs a command it can
   run, not an intention.
2. **Use an existing one under a different name.** If tests exist but `init` did not recognise the
   command (a custom runner, a container, a different toolchain), put it in `Full suite:` and run
   it once before confirming.
3. **Do not use this plugin for this bug.** A run without a baseline cannot prove it fixed
   anything. Fix the bug directly and record why the plugin was not used.

Option 3 is an acceptable outcome. Pretending to run the phases without a suite is not.

## What you must not do here

- **Do not guess a command.** Run it, or write `none` and say why.
- **Do not invent a search tool name.** If you cannot call it, it does not belong in the field.
- **Do not copy secrets.** Names only.
- **Do not fill the file by hand from memory of another project.** That is how a contract
  describes a repository it was never written for.
