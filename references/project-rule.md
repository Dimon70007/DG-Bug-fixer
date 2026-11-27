# bug-fixer: mandatory project rule

> **The block between the markers is what the harness reads.** It is written and merged by
> `install.py`, and belongs to the plugin: edit inside it only by changing the plugin, because a
> re-install rewrites it.

This is the **main initialisation channel** (spec §18.3, invariant 41). A session-start hook
cannot be relied on — Cursor accepts `additional_context` and does not deliver it to the model
(vendor bug since April 2026, no ETA, §18.4) — while a rules file is read by the harness itself
with no hooks involved. The rule is static and cheap; it sends the agent to a **dynamic** check
that it performs itself.

## Why a rule and not only a gate

The gate refuses the first unauthorised write, which is the mechanism that cannot be bypassed.
Refusing is late, though: by then the agent has read code, formed a plan and possibly written a
test against a command that does not exist. The rule lets it learn the same fact before the first
edit, in every harness, at the cost of a few lines of context.

## Content

The block is deliberately short and imperative:

| Requirement | Why |
|---|---|
| run `bugfixer doctor` before the first edit | it is the only thing that knows whether the contract, the ledger and the test command are usable |
| a non-zero exit means initialise first (`references/init.md`), then work | an uninitialised project looks exactly like a working one until the gate refuses |
| say plainly that the gate will refuse edits until the contract is confirmed | the refusal is predictable, not a malfunction |

It names no path, no test command and no harness-specific syntax: those are the project's
answers, held in the contract, and the check reads them. A rule that repeated them would be a
second copy to keep in step — and the copy the agent trusts would be the stale one.

## Where it lands

| Harness | File | Registration |
|---|---|---|
| Cursor | `.cursor/rules/bug-fixer.mdc` | `alwaysApply: true` |
| Claude Code | `CLAUDE.md` | one-line import of `AGENTS.md` |
| Codex | `AGENTS.md` | read from the repository root |
| OpenCode | `AGENTS.md` | read; falls back to `CLAUDE.md` |
| OpenClaw | `AGENTS.md` | injected into the project context |

**In the project, not in the plugin bundle.** This is deliberate and was checked, not assumed: a
rule that ships inside a plugin (`<plugin>/rules/*.mdc`) is read, but Cursor **downgrades** its
`alwaysApply: true` to "agent decides when to apply" — a confirmed vendor bug, reported on team
plugins with no fix date. A rule that the agent may or may not pull in is exactly the kind of
silently-satisfied requirement §18.7 forbids relying on, so the installer writes the rule into the
project's own `.cursor/rules/`, where `alwaysApply` works as documented.

**Merged, never overwritten.** `AGENTS.md` and `CLAUDE.md` almost always exist already and belong
to the project. The installer adds its block with a marker, keeps everything else and does not
duplicate the block on a re-run.

## Turning it off

Removing the block is what `install.py --uninstall` does, and it is the documented way to stop the
plugin enforcing anything here. Editing inside the block by hand is not: the next install
overwrites it, and the two copies then disagree about the project's state.
