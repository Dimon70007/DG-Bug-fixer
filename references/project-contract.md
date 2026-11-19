# Project contract — template

`bug-fixer` is generic. Everything project-specific lives here, in a file the skill
reads before Phase 1.

## How the skill finds this file

In order:

1. `bug-fixer.project.md` in the project root.
2. A path the user named explicitly.
3. Nothing found — the skill asks, and writes this file using the template below.

The file name matters: it is what the skill looks for. Do not bury the values in a
`README` or in chat history.

## Rules for filling it in

- **Every field is required.** A missing field means the agent guesses, and a guessed
  test command is worse than no command: it fails and reads like a broken build.
- **If something is not allowed, write that.** "No web search" is a valid, useful answer.
  An empty field is not.
- **Use the real command, not a description of it.** `cargo test --all-features`, not
  "run the test suite".
- Keep it short. This file is read at the start of every bug, so it competes for context
  with the actual problem.

---

## Template — copy this block into `bug-fixer.project.md`

```markdown
# bug-fixer contract: <project name>

## Ledger

- Task/bug file: <path or pattern, e.g. tasks/TASK-*.md | issues/ | docs/bugs/NNN.md>
- Findings go here as `F<n>` blocks, appended, never reordered.
- Raw dumps too large for the ledger: <path, e.g. logs/<task>-<stamp>-knowledge/>
- Second ledger: NOT allowed. No DEBUG.md, no separate notes file.

## Tests

| What | Command |
|---|---|
| Full suite (the gate) | <e.g. make test> |
| One file | <e.g. pytest tests/test_foo.py -v> |
| Focused/unit only | <e.g. cargo test -p my-crate> |
| Lint / typecheck | <e.g. ruff check . && mypy> |

The full-suite command is what Phase 1 records as baseline and Phase 3 reruns.

## Search (Phase 2.1)

- Allowed: <e.g. tavily_search / agent-search MCP | none>
- Forbidden: <e.g. curl, wget, WebFetch to external hosts>
- If `none`: skip Phase 2.1 and build hypotheses from Phase 1 evidence only.

## Extra prohibitions for this project

- <e.g. no sudo from the agent; print the command for the operator>
- <e.g. no destructive commands: no rm -rf /, no docker prune>
- <e.g. dependencies only via poetry; never pip install into a system interpreter>
- <e.g. never send secrets, tokens, or PII to a search query or a cloud model>
```

---

## Filled example — a Python service with a Makefile

```markdown
# bug-fixer contract: billing-service

## Ledger

- Task/bug file: tasks/TASK-*.md (one file per bug; new file if none exists)
- Findings go here as `F<n>` blocks, appended, never reordered.
- Raw dumps too large for the ledger: logs/<task>-<stamp>-knowledge/
- Second ledger: NOT allowed. No DEBUG.md, no separate notes file.

## Tests

| What | Command |
|---|---|
| Full suite (the gate) | make test |
| One file | poetry run pytest tests/test_billing.py -v |
| Focused/unit only | poetry run pytest -m "not e2e" |
| Lint / typecheck | poetry run ruff check . && poetry run mypy src |

## Search (Phase 2.1)

- Allowed: tavily_search, tavily_extract
- Forbidden: curl, wget, WebFetch to external hosts
- Never put API keys, tenant ids, or customer data into a query.

## Extra prohibitions for this project

- No sudo from the agent; print the command for the operator instead.
- Dependencies only via poetry; never pip install into a system interpreter.
- Do not modify historical test snapshots; add new ones.
```

## Filled example — a Rust CLI with no web access

```markdown
# bug-fixer contract: logrotate-rs

## Ledger

- Task/bug file: docs/bugs/NNN-<slug>.md
- Findings go here as `F<n>` blocks, appended, never reordered.
- Raw dumps too large for the ledger: target/bugfix-<stamp>/
- Second ledger: NOT allowed.

## Tests

| What | Command |
|---|---|
| Full suite (the gate) | cargo test --all-features |
| One file | cargo test --test cli_parse |
| Focused/unit only | cargo test --lib |
| Lint / typecheck | cargo clippy -- -D warnings |

## Search (Phase 2.1)

- Allowed: none
- Forbidden: curl, wget, WebFetch, any network access
- Skip Phase 2.1 entirely; build hypotheses from Phase 1 evidence.

## Extra prohibitions for this project

- No network access during tests.
- No sudo from the agent.
```

---

## When the contract is wrong

If a command in this file does not work, that is a bug in the contract, not a licence to
improvise. Fix the contract, note the change in the ledger as a finding, and continue.
The next bug benefits; an improvised command benefits nobody.
