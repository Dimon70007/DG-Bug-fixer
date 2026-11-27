# bug-fixer contract: scratch project

## Ledger

Task/bug file: tasks/TASK-*.md
Docs paths: docs/

Findings go into that file as `F<n>` blocks, appended, never reordered. A second ledger is
not allowed: no `DEBUG.md`, no separate notes file.

## Tests

Full suite: python -m pytest -q
Test paths: tests/

## Search

Allowed: none

If `none`: skip step 3.3 and build hypotheses from Phase 1 evidence only.

## Extra prohibitions for this project

- no sudo from the agent; print the command for the operator
- dependencies only via poetry; never pip install into a system interpreter

Contract version: 1
Contract verified by: agent 2026-11-27 — doctor passed, suite runs
