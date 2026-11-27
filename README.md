# bug-fixer

A five-phase debugging skill for coding agents. It stops the agent from writing a fix before
the cause is known, and it makes every fix a probe of a **named** hypothesis.

```
Phase 0 (setup, outside the automaton: project contract + ## Clarifications)

Phase 1 (baseline) --REPRODUCED--> Phase 2 (route)
                                      |         |
                        ALREADY_TRIED |         | FIRST_GUESS
                                      v         v
                            Phase 3 (research) --HYPOTHESIS_READY--> Phase 4 (probe)
                                      ^                                   |
                                      |                                   | PROVEN
                    QUEUE_EXHAUSTED / DEEPEN_SAME                         v
                                      |                            Phase 5 (decide)
                                      +-----------------------------------+

Phase 4 is a loop: NEXT_IN_QUEUE (4->4) takes the next hypothesis by Rank.
Terminals by status, not by edge: BLOCKED, LIMIT_REACHED (A = 10), DONE, PARTIAL.
```

- **Phase 1** builds a failing test, an e2e test, and a full-suite baseline. No production
  code changes are allowed yet, and the task must be clarified first: 3–7 `Q:`/`A:` pairs, or
  a named `Clarification gap:`, or explicit `Assumption:` lines in a headless run.
- **Phase 2** routes. If an `Attempt <k> consumed` line already exists and nothing is
  confirmed, it goes to research; otherwise it writes exactly one obvious hypothesis `H0` and
  goes straight to the probe phase. It does not edit code.
- **Phase 3** researches: collects logs, searches by the exact lines it found, and writes each
  candidate as `### H<n>`. A `Merge:` line records whether any predictions coincided.
- **Phase 4** probes one hypothesis at a time, by `Rank`. RED on the target tests, then the code
  change (`Attempt`), then GREEN on the full suite. Each iteration writes its own
  `### F<n>` block and ends in a verdict.
- **Phase 5** runs only after `CONFIRMED`. Four conditions must hold at once: the RED test
  passes, the e2e test passes, no baseline-green test turned red, and debug logging is gone.
  Then it picks `DONE`, `PARTIAL` (with `## Remaining`), or `BLOCKED` (with `Причина` and
  `Логи`).

**Eight named edges, and two of them lead `4→3`.** `QUEUE_EXHAUSTED` (refuted, nothing left to
probe → research again) and `DEEPEN_SAME` (partially right → deepen the same hypothesis) are
different verdicts with different proofs. They are why the transition table is keyed by edge
name rather than by phase pair: a pair-keyed dict holds one of them and silently loses the
other.

Two counters, both derived from the ledger:

| Counter | Counts | Limit |
|---|---|---|
| `A` | probes that reached a code change (`Attempt <k> consumed`) | **10** |
| `R<h>` | re-runs of one hypothesis's target tests (`Recheck H<n> #<k>`) | **5** |

`Recheck` does **not** consume `A`: repairing a broken test is repairing the instrument, not
new work. `A` can be reset only by the operator, on a `Counter reset: A -> 0 (by operator: …)`
line.

Findings go into the **target task file** as `H<n>` and `F<n>` blocks. This skill does not
create a `DEBUG.md`; one ledger, not two.

### It is project-agnostic, and it adapts before it works

The five phases are the same everywhere. The commands are not: a Rust crate runs
`cargo test`, a Python service runs `make test`, and some projects have no web access at
all. So before Phase 1 the skill reads a **project contract** and takes four values from it:

- where findings go,
- the full-suite test command (the gate),
- which search tools are allowed, if any,
- extra prohibitions for that project.

Lookup order: `bug-fixer.project.md` in the project root, then a path you name, then the
skill **asks** and writes the file using the template. It does not guess a test command.
See [`references/project-contract.md`](references/project-contract.md) for the template and
two filled examples.

Validate the result with `bugfixer doctor`: it reads the file by the same rules the gate
does, so a contract it accepts is one the gate can actually enforce. Two fields deserve a
word, because both used to be wrong:

- **`Test paths:`** is required, and it is *not* a fifth requirement. It is a detail of the
  full-suite point: `§1` forbids production edits before the cause is confirmed while
  allowing test edits, so the gate must be able to tell them apart, and `§0` forbids
  guessing a path. The contract therefore carries it inside point 2.
- **`Raw dumps:`** is optional. Without it, logs too large for the task file stay in the
  task file (`§5.3`); `doctor` warns and exits `0`.

Before stage 2 the template omitted `Test paths:` altogether, so a project that followed the
template produced a gate that could not find its own ledger and silently allowed every write.

---

## What is in here

```
bug-fixer/
├── SKILL.md                      # the skill: phases, edges, counters, prohibitions
├── .cursor-plugin/
│   └── plugin.json               # Cursor plugin manifest (skill root = the skill)
├── LICENSE                       # MIT
├── install.py                    # installer (not copied into targets)
├── install.sh                    # argv forwarder for install.py
├── references/
│   ├── workflow.md               # discriminators, results tables, anti-patterns
│   ├── init.md                   # step 0a: confirm what `init` derived
│   ├── project-rule.md           # the rules file: why the main channel is not a hook
│   └── project-contract.md       # TEMPLATE: fill this for your project
├── bin/
│   └── bugfixer                  # single entry point: `bugfixer doctor` / `init`
├── lib/
│   └── bugfixer/
│       ├── contract.py           # the contract schema: four §6 points, one reader
│       ├── readiness.py          # is this project ready? one answer for doctor + hook
│       └── rules.py             # the project rule: written, merged, removed
├── tests/                        # the plugin's own suite (not copied into a target)
│   ├── fixtures/
│   │   └── canonical-ledger.md   # spec §14 example, guarded for equality in the parent
│   └── test_bugfixer_*.py
├── hooks/                        # optional enforcement gate + harness matrix
│   ├── bugfixer_gate.py
│   ├── bugfixer_gate_state.py
│   ├── bugfixer_secrets.py       # secret scanner for records and diffs
│   ├── run-gate.sh               # portable preToolUse entry point (no absolute paths)
│   ├── session-start             # portable sessionStart entry point (§18.4, best-effort)
│   ├── session_start.py          # what to say, and which adapter says it
│   ├── adapters/                 # one file per harness: cursor, claude, codex, opencode, openclaw
│   ├── hooks-cursor.json         # Cursor-format hooks used by the plugin manifest
│   ├── cursor.hooks.json         # template for a project-level hook, if you wire it by hand
│   ├── verify_harnesses.py
│   └── *.json, *.ts              # adapter templates per agent
├── runner/
│   └── bugfixer_run.py           # optional autonomous loop
└── templates/
    ├── finding-block.md          # F<n> block to append to the task file
    └── hypothesis-block.md       # H<n> block, one format for H0 and H1…Hn
```

`SKILL.md` is the only file loaded up front. Everything else is optional tooling: the skill
works by copying this folder, and the gate and runner are extra layers you opt into.

**The skill is self-contained.** The installer, the gate, and the runner all live inside this
directory, so a clone of this repository installs itself without any parent project.

---

## Before you install: what the agent must be able to do

The skill is instructions, not code. It can do nothing the agent cannot already do.

| Capability | Needed for | If missing |
|---|---|---|
| Edit files in the repo | writing tests, logging, the ledger | skill cannot run; nothing to fall back on |
| Run the project's test command | Phase 1 baseline, Phase 4 GREEN gate | blocked at Phase 1: no baseline means no regression check |
| Write to the ledger path | recording findings | skill stops and asks for a path |
| A search tool, if the contract allows one | Phase 3 steps 3.3–3.4 | those steps are skipped; hypotheses come from Phase 1 evidence |

A read-only agent can reproduce and hypothesise, but it cannot complete Phase 4, because that
phase requires writing a fix and rerunning the suite.

---

## Install

The format is the same everywhere: a folder named `bug-fixer` containing `SKILL.md`.
Pick the destination, then restart the session (or start a new chat) so the skill metadata
is re-read.

**Two ways in, and they install different things.** Read this before choosing, because the
difference is a working gate versus a skill with no enforcement.

| Route | What you get | Hooks |
|---|---|---|
| **Plugin** (`/add-plugin`, or a local plugin folder) | skill + manifest, managed by Cursor | wired by the manifest — **IDE only**, see the warning below |
| **Installer** (`./install.sh --target …`) | skill copied into a project or home directory | project `.cursor/hooks.json` written for you, works in **IDE and CLI** |

### Route A — plugin

Documented in Cursor's plugin reference: a plugin is a directory with a manifest and its
components, and every Cursor plugin needs `.cursor-plugin/plugin.json`. This repository ships
one at the skill root, so the skill directory *is* the plugin:

```json
{
  "name": "bug-fixer",
  "version": "3.1.0",
  "hooks": "./hooks/hooks-cursor.json"
}
```

- The root `SKILL.md` is the skill. There is deliberately **no** `skills` field: Cursor treats
  `SKILL.md` at the plugin root as a single-skill plugin only while no `skills/` directory and no
  manifest `skills` field exist. Adding one would hide the skill.
- `hooks` points at a Cursor-format hook file (`version: 1`, camelCase events). The command is
  relative (`./hooks/run-gate.sh`) because a plugin cannot embed an interpreter path that
  differs per machine. Cursor runs plugin hooks from the plugin directory for every event except
  `stop`.
- Install it with `/add-plugin` from a marketplace, or drop the directory under
  `~/.cursor/plugins/local/` for a local test.

> **Plugin hooks do not run in the Cursor CLI.** This is a documented, still-open gap: the CLI
> loads a plugin's skills and rules but ignores its hooks. If you use `cursor-agent`, add the
> hook file route below as well — that path *is* honoured by the CLI.

### Route B — installer (recommended: the only route where the gate is enforced everywhere)

The skill carries its own installer, so a clone needs nothing else:

```bash
git clone https://github.com/Dimon70007/DG-Bug-fixer.git ~/src/bug-fixer
cd ~/src/bug-fixer
./install.sh --target cursor                            # into the git checkout this clone sits in
./install.sh --target cursor --project ~/work/my-repo   # into another project
./install.sh --target home --home ~                     # ~/.cursor/skills; --home is mandatory
./install.sh --target personal                          # ~/.cursor/skills, every project
./install.sh --target all --dry-run                     # show the plan, write nothing
./install.sh --target cursor --project ~/work/my-repo --uninstall
```

**`--target home` versus `--target personal`.** Both install into a home directory, and the
difference is that `home` **requires** `--home <path>`. `personal` defaults to `~`; `home` refuses
to guess, because a machine with several users or a shared volume otherwise installs into a home
nobody chose. Use `home` when the path should be stated explicitly, `personal` for the convenience
default.

**A home install writes no hook.** There is no project root to attach it to, so the skill arrives
without enforcement and the installer says so. The gate is wired per project: either re-run the
installer with `--target cursor --project <repo>`, or copy the hook entry by hand.

**Removing it.** `--uninstall` removes the skill directory **and** this skill's entry from
`.cursor/hooks.json`, `.claude/settings.json` and `.codex/hooks.json`, leaving every other hook
untouched. This is not optional politeness: a leftover entry points at a deleted wrapper, and with
`failClosed: true` that blocks every tool call in the project — the agent reports a broken guard
rather than a missing skill.

For a project-scope target the installer also writes `<project>/.cursor/hooks.json`:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      {
        "command": "\"$CURSOR_PROJECT_DIR/.cursor/skills/bug-fixer/hooks/session-start\" cursor",
        "timeout": 10
      }
    ],
    "preToolUse": [
      {
        "command": "\"$CURSOR_PROJECT_DIR/.cursor/skills/bug-fixer/hooks/run-gate.sh\"",
        "timeout": 10,
        "failClosed": true
      }
    ]
  }
}
```

`sessionStart` carries the harness id as its argument: the harness chose the adapter by
registering this file, and the adapter is the only thing that knows that harness's JSON shape.

Three details, each from Cursor's hooks documentation and each load-bearing:

1. **Project hooks resolve from the project root**, so the path is
   `.cursor/skills/…/run-gate.sh` — *not* `./hooks/…`, which Cursor would look for at
   `/hooks/…`.
2. **`failClosed: true`** turns a crash, timeout or empty output into a block instead of an
   allow. Without it a broken hook silently stops protecting anything.
3. **An existing `hooks.json` is merged, not replaced.** Other hooks in that project survive;
   only this skill's own entry is refreshed, so re-running the installer is idempotent.

A user-scope install (`--target personal`) writes no hook: there is no project root to attach
it to, and creating `.cursor/hooks.json` in an unrelated directory would be a surprise.

> The clone uses HTTPS so it works without an SSH key. Replace it with
> `git@github.com:Dimon70007/DG-Bug-fixer.git` if you have a key set up.

### What each harness gets

One gate, one wrapper, five wirings. The difference between harnesses is the **config file
shape**, never the logic — so a fix in `bugfixer_gate.py` reaches all of them at once.

| Harness | Skill location | Hook config | How the wrapper path is resolved |
|---|---|---|---|
| Cursor | `.cursor/skills/` | `.cursor/hooks.json` → `preToolUse` | `$CURSOR_PROJECT_DIR` |
| Claude Code | `.claude/skills/` | `.claude/settings.json` → `PreToolUse` | `${CLAUDE_PROJECT_DIR}` |
| Codex | `.agents/skills/` | `.codex/hooks.json` → `PreToolUse` | `git rev-parse --show-toplevel` (Codex documents no project variable) |
| OpenCode | `.opencode/skills/` | `.opencode/plugin/bugfixer-gate.ts` (in-process `tool.execute.before`) | plugin gets `cwd` from the tool call |
| OpenClaw | `<workspace>/skills/` | OpenClaw plugin `before_tool_call` | plugin gets `cwd`/`workdir` from the event |

No path is baked in at install time. That is deliberate: an absolute path is correct on
exactly one machine, and the failure is silent — the hook points at a file that is not
there, `failClosed` blocks every tool call, and the agent reports a broken guard.

### Where the agent learns it must check first — a rule, not a hook

The gate refuses the first unauthorised write, which is the mechanism that cannot be bypassed,
but it refuses **late**: by then the agent has read code and planned against a test command that
may not exist. So the main initialisation channel is a **rules file**, which the harness reads
itself with no hook involved:

| Harness | File | Registration |
|---|---|---|
| Cursor | `.cursor/rules/bug-fixer.mdc` | `alwaysApply: true` |
| Claude Code | `CLAUDE.md` | one-line import `@AGENTS.md` |
| Codex / OpenCode / OpenClaw | `AGENTS.md` | read from the repository root |

The block is five lines: *run `bugfixer doctor` before the first edit, and a non-zero exit means
initialise first (`references/init.md`)*. It names no test command and no ledger — those are the
project's answers, held in the contract, and the check reads them. A copy in the rule would be a
second place to keep in step, and the stale copy is the one the agent would trust.

The rule is written into the **project**, not shipped inside the plugin bundle: Cursor downgrades
`alwaysApply: true` for rules that come from a plugin to "agent decides" (confirmed vendor bug, no
fix date), so a bundled rule would arrive as a suggestion. Both entry points place it —
`install.py` for the harness being installed into, and `bugfixer init` for all three files at once,
because a plugin installed from a marketplace never runs the installer and would otherwise leave
the project with a gate and no instruction. `AGENTS.md` and `CLAUDE.md` belong to the project too,
so the installer **merges** its marked block and never replaces the file; a re-install does not
stack a second copy, and `--uninstall` removes exactly the block it added. See
[`references/project-rule.md`](references/project-rule.md).

The `sessionStart` hook is registered as well, and it is **best-effort**: Cursor runs it, accepts
the JSON and does not deliver `additional_context` to the model (vendor bug since April 2026,
confirmed by Cursor staff, no ETA). Nothing depends on delivery — the rule says the same thing and
the gate enforces it. When the channel starts working, the message gains precision without any
change to the wiring.

Two harness behaviours worth knowing before you rely on this:

- **Codex has no project-directory variable** in hooks. Its own examples resolve the repo
  root with `git rev-parse --show-toplevel`, so the template does the same. A project that
  is not a git checkout has no working Codex hook — the installer says so rather than
  writing a command that silently does nothing.
- **OpenCode does not fire `tool.execute.before` for subagent calls** (`anomalyco/opencode#5894`),
  so its plugin enforces the loop for the primary agent only. This is a harness limit, not a
  configuration mistake.



### Nothing is machine-specific

The gate contains no absolute path. It finds the ledger in this order:

1. `--ledger` from the adapter, when one is configured;
2. a ledger path in the hook payload, when the harness sends one;
3. **the project contract** — `Task/bug file: tasks/TASK-*.md` — resolved from the hook's working
   directory, newest match wins.

So the same installed tree works on any machine. If the pattern matches two files that cannot be
ordered, or matches nothing, the gate stays inert rather than attaching the run to a guessed bug.

### Verifying the install

```bash
python3 hooks/verify_harnesses.py
```

It runs the gate against a synthetic deny first, then reports one row per harness:
`verified` / `not-wired` / `not-installed` / `not-automatable` / `found-outside-path`. A harness
that is absent or needs an interactive sign-in is **not** reported as a pass — `verified denies:
0 of 5` is the honest answer on a machine where nothing was reproduced, and it is the answer this
repository reports.

`found-outside-path` exists because a binary can be installed and still be invisible:
`cursor-agent` lives in `~/.local/bin`, which is not on `PATH` by default on macOS. The verifier
says so instead of calling the harness missing.

The per-agent sections below give the destination each target means; the commands are the
same `./install.sh --target …` in every case.

### Cursor

| Scope | Path |
|---|---|
| Project | `.cursor/skills/bug-fixer/` |
| Every project | `~/.cursor/skills/bug-fixer/` |

```bash
./install.sh --target personal     # every project
./install.sh --target cursor       # the current project
```

Cursor also reads `.agents/skills/` and `~/.agents/skills/`, and the Claude and Codex
directories for compatibility. Invoke with `/bug-fixer`, or just describe a failing test and
let the description trigger it.

### Claude Code

| Scope | Path |
|---|---|
| Project | `.claude/skills/bug-fixer/` |
| Every project | `~/.claude/skills/bug-fixer/` |

```bash
./install.sh --target personal-claude     # every project
./install.sh --target claude              # the current project
```

### Codex

Official locations, per OpenAI's skill docs:

| Scope | Path |
|---|---|
| Project | `.agents/skills/bug-fixer/` (also scanned in every directory up to the repo root) |
| Every project | `$HOME/.agents/skills/bug-fixer/` |
| Admin / machine-wide | `/etc/codex/skills/bug-fixer/` |

```bash
./install.sh --target personal-agents     # every project
./install.sh --target codex               # the current project
```

Invoke with `$bug-fixer` or `/skills` to browse. Older guides mention `~/.codex/skills/`;
OpenAI's current documentation lists `.agents/skills` for repo and user scope.

### OpenCode

| Scope | Path |
|---|---|
| Project | `.opencode/skills/bug-fixer/` |
| Every project | `~/.config/opencode/skills/bug-fixer/` |

```bash
./install.sh --target personal-opencode   # every project
./install.sh --target opencode            # the current project
```

The global path is `~/.config/opencode/skills`, **not** `~/.opencode/skills`; several
third-party guides get this wrong. OpenCode also reads `.claude/skills` and `.agents/skills`
in both scopes, so an install there works for OpenCode too.

### OpenClaw

| Scope | Path |
|---|---|
| Workspace (highest precedence) | `<workspace>/skills/bug-fixer/` |
| Managed / shared | `~/.openclaw/skills/bug-fixer/` |

```bash
./install.sh --target openclaw --dest ~/workspace
```

Or install from the registry or straight from Git:

```bash
openclaw skills install git:Dimon70007/DG-Bug-fixer
```

Verify and inspect:

```bash
openclaw skills list
openclaw skills info bug-fixer
openclaw skills check
```

OpenClaw snapshots eligible skills at session start, so a mid-session install appears on the
next conversation. The skills watcher normally picks up changes sooner.

### One command for several agents

If you have this repo checked out, `scripts/common/install_ai_skill.py` installs into any
combination of the destinations above. It is stdlib-only, so Python 3.9 is enough.

```bash
python3 scripts/common/install_ai_skill.py bug-fixer --target cursor
python3 scripts/common/install_ai_skill.py bug-fixer --target all          # 4 in-repo dirs
python3 scripts/common/install_ai_skill.py bug-fixer --target personal     # ~/.cursor/skills
python3 scripts/common/install_ai_skill.py bug-fixer --target openclaw --dest ~/workspace
python3 scripts/common/install_ai_skill.py bug-fixer --target personal --source ~/src/dg-bug-fixer
```

| `--target` | Destination |
|---|---|
| `cursor` | `.cursor/skills/<id>/` |
| `codex` | `.agents/skills/<id>/` |
| `claude` | `.claude/skills/<id>/` |
| `opencode` | `.opencode/skills/<id>/` |
| `personal` | `~/.cursor/skills/<id>/` |
| `personal-agents` | `~/.agents/skills/<id>/` |
| `personal-claude` | `~/.claude/skills/<id>/` |
| `personal-opencode` | `~/.config/opencode/skills/<id>/` |
| `personal-openclaw` | `~/.openclaw/skills/<id>/` |
| `openclaw` | `<DEST>/skills/<id>/` — `DEST` required |
| `all` | the four in-repo targets only |

Unknown target, unknown skill, or a `--source` without `SKILL.md` exits non-zero. Nothing
falls back silently.

---

## Use

Automatic: describe the problem. "These tests started failing after my change" or "this
returns the wrong value for empty input" matches the description and the skill loads.

Explicit:

| Agent | How |
|---|---|
| Cursor | `/bug-fixer` |
| Claude Code | `/bug-fixer` |
| Codex | `$bug-fixer`, or `/skills` to browse |
| OpenCode | `/skills`, or name it in the prompt |
| OpenClaw | `/skill bug-fixer` |

Give it a task file to write findings into. That file becomes the ledger:

```
Use bug-fixer on tasks/TASK-123-null-deref.md
```

On a project that has no `bug-fixer.project.md` yet, the first run asks four questions and
writes one. That is expected, not friction: it is the difference between a skill that works
in your repo and one that assumes it.

The skill will not touch production code until an experiment confirms the cause. That
prohibition is the point: a fix without a confirmed cause is a guess that happens to pass
one test.

### What a run looks like

A real one, from a Metal backend bug (`MUL_MAT(tq3_1s|tq4_1s, k_v=1600)`), one iteration,
six experiments, `DONE`:

| Phase | What happened | Recorded as |
|---|---|---|
| 1 | Two cases red in ~2 s; full op sweep baselined at `1652/1654` | `F5` |
| 2 | `TQ_NO_ROTATE=1` turned both green → the codec was not at fault, the path was | `F6` |
| 2 | Same shape with `k_v=0` was already green and used the same path → **layout**, not shape | `F7` |
| 2 | Kernel takes only `(x, n)` and walks the buffer flat → needs contiguous input | `F8` |
| 2 | The MoE variant had the same missing gate, unfalsifiable by any existing test | `F9` |
| 3 | Fix: gate the branch on contiguity. `0/2`→`2/2`, sweep `1654/1654`, no new reds | `F10` |

The experiment that did the work was the third one. Before it, two hypotheses predicted the
same observation and neither could be killed. The full blocks are in the task file of the
project that ran this study; the plugin itself carries no project-specific history.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Skill never appears | Wrong path, or `skill.md` in lowercase — the filename is case-sensitive |
| Appears after a restart only | Metadata is read at session start; that is expected |
| Not triggered automatically | The `description` must name the situation; vague descriptions are ignored |
| Duplicate name warnings | Two skills share a `name`; only one wins |
| Skill stops and asks four questions | No `bug-fixer.project.md` in the project yet — answer them once |
| Agent invents a test command | The contract is missing or has an empty `Tests` row |

Frontmatter must use spaces, not tabs, and must carry `name` and `description`. The directory
name should match `name`.

---

## Optional: run the loop autonomously

The gate stops a dishonest move inside a pass. Nothing stopped a *human* from having to start
each pass. The harness does:

```bash
make bugfixer-run LEDGER=tasks/TASK-123.md TEST_CMD='make test' AGENT=cursor
```

It runs the agent headlessly, phase by phase, and stops on evidence **the agent cannot fake**:

| Condition | Result |
|---|---|
| the suite exits `0` | `DONE` |
| `--max-passes` reached | `BLOCKED`, never `DONE` |
| the same failing test set twice with no ledger change | `BLOCKED` — no progress |
| ledger already `DONE`/`BLOCKED` | stop |

The agent's own "all tests pass" is ignored. A run whose suite is red ends `BLOCKED` even if the
agent wrote `STATUS: DONE` — that is asserted in `tests/test_bugfixer_harness.py`, because it is
the single most common failure mode of autonomous agents. A green suite **without** a results
table is also `BLOCKED`: a fix whose effect was never measured is the case `PROVEN` refuses, and
reporting it as done would claim success for a change nobody can check. `A = 10` ends the run
with `LIMIT_REACHED`.

Artifacts per run: `logs/bugfixer-run-<stamp>/pass-NN/{prompt,agent.log,tests.log,result.json}`,
plus one `F<n>` block per pass appended to the ledger. That block carries `Kind | research` and
claims **no** verdict: a verdict belongs to the agent that saw the logs, or the pipeline would
prove its own hypothesis.

**Prerequisites.** `CURSOR_API_KEY` for `AGENT=cursor`, exported in the environment — never on the
command line, where it would land in shell history. `AGENT=command AGENT_CMD='./my-agent {prompt}'`
plugs in any other harness.

**What it will not do.** No `sudo` (it refuses, and refuses to run as root), no parallel agents,
no PRs. It also does not replace the gate: install `hooks/` as well, or the agent can still edit
production code outside Phase 4 within a pass.

---

## Optional: enforce the loop with hooks

The rules above are text. An optional gate turns them into mechanisms: the attempt cap, the
"Phase 4 only" write ban, the required `Working on:` marker, the counter numbering, the secret
scan, and "no writes after a terminal status".

```
hooks/
├── bugfixer_gate.py         # the decision (reads a harness payload on stdin)
├── bugfixer_gate_state.py   # eight named edges; appends the run journal
├── bugfixer_journal.py      # the journal: append-only, runtime-written (spec §5.4)
├── bugfixer_secrets.py      # scanner; usable as --diff by the agent itself
├── cursor.hooks.json        # template -> .cursor/hooks.json
├── claude.settings.json     # template -> .claude/settings.json
├── codex.hooks.json         # template -> .codex/hooks.json
├── opencode-plugin.ts       # -> .opencode/plugin/bugfixer-gate.ts
└── openclaw-plugin.ts       # OpenClaw plugin entry
```

### How strong is it, per harness

| Harness | Mechanism | Blocks the primary agent | Blocks subagents |
|---|---|---|---|
| Cursor | `preToolUse` + `failClosed: true` | yes | tool hooks **do** fire in subagents, but the payload carries no `agent_id`; `sessionStart` is absent in cloud agents |
| Claude Code | `PreToolUse` + `onFailure: block` | yes | **contested** — the hooks reference says they run in subagents with `agent_id`; [#88441](https://github.com/anthropics/claude-code/issues/88441) reports otherwise. Do not claim it without naming the version |
| Codex | `PreToolUse` (must be **trusted**; a changed definition is skipped until re-trusted) | yes | not documented |
| OpenCode | `tool.execute.before` → `throw` | yes | **no** — see [#5894](https://github.com/anomalyco/opencode/issues/5894) |
| OpenClaw | `before_tool_call` → `{ block: true }` | yes | unknown — `spawn_agent` is gateable, the child's own calls are undocumented |

Read that table before trusting it. For the full nine-column matrix — per-harness hook shapes,
path rules, invocation syntax, tool vocabularies, each cell sourced or marked *unknown* — see
`docs/skill-harness-matrix.md` in the parent repository. It is not shipped with the skill: a
standalone clone installs only this directory, so the link would be dead there. The gate is
**strong for the primary agent** and **advisory wherever a subagent can be spawned**. Claiming
otherwise would be false.

### Two footguns the templates already handle

- **Claude Code exit `1` blocks nothing.** A crashing guard looks like permission. The template
  sets `onFailure: "block"`.
- **Cursor fails open by default** on crashes, timeouts, and non-`2` exits. The template sets
  `failClosed: true`.

### What the gate decides

| Condition | Writes denied |
|---|---|
| Ledger is a 2.1 file (`## Iteration N`, no `### H<n>`, no `Kind` field) | all, with "finish it under 2.1 or start a new ledger" |
| `STATUS: BLOCKED`, `DONE`, `PARTIAL`, `LIMIT_REACHED` in the ledger | all |
| a `STATUS:` word the gate does not know | all — an unreadable status is not "probably RUNNING" |
| Phases 1, 2, 3 and 5, target outside the contract's `Test paths:` | that write |
| Phase 4 without `Working on: H<n>` pointing at an existing hypothesis | that write |
| two `Working on:` lines, or `Attempt`/`Recheck` written as a table row | that write |
| attempt numbering with a gap, `Recheck #6`, or an 11th hypothesis (checked on a transition) | the transition |
| a secret in a `### F<n>` record | that write |
| anything unreadable (corrupt journal record, no `Test paths:`) | all, with the reason |

`A` is counted from the `Attempt <k> consumed` lines, after the last operator reset. A hook
sees tool calls, not loops, so the ledger is the only honest source. The phase cannot be
inferred at all, so it is read from the run journal — one appended record per event, written
by the state CLI and never by the agent (spec §5.4, invariants 32–36):

```bash
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md           # phase, status, hypothesis
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md edges     # the eight names
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md advance 2 --evidence "RED + baseline + 3Q"
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md advance 3 --edge DEEPEN_SAME --evidence "delta 7->2"
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md status BLOCKED
```

The journal is `tasks/TASK-123.md.bugfixer.jsonl`, beside the task file and outside git
(`*.bugfixer.jsonl`). It is append-only: `advance` grows it by one line and rewrites nothing.
A write to it through a tool is refused, naming the gate as its owner — there is no phase
number for an agent to edit, which is what the old `.bugfixer.json` sidecar allowed.

A phase pair with two meanings (`4→3`) **requires** `--edge`: the gate refuses to guess which
one you meant, because they differ in verdict, proof and what happens next.

### Not enforced

- **Revert on degradation.** The gate cannot tell a revert from a rewrite; it can only require
  the `Revert:` line.
- **Whether a change really was a probe of the named hypothesis.** The marker is checked, the
  edit's meaning is not. A human reads that in the report.
- **Whether two target tests cover different behaviour.** The gate counts them; it does not
  judge them.
- **Whether `Resolution:` is a good criterion.** Presence is checked; quality is read.
- **That a flaky bug really got three runs.** One `ФАКТ (RED):` marker does not count repeats.
- **Subagent bypass.** Not fixable from a hook; documented above.
- **"Is this write really needed".** The gate checks paths, markers and status, not intent.

---

## Limitations, stated plainly

- **No enforcement.** The skill is text. Nothing mechanically prevents an agent from editing
  production code outside Phase 4. The prohibition is written as a hard rule in `SKILL.md` and
  repeated in the contract, which improves compliance; it does not guarantee it.
- **No counter in code unless the gate is installed.** `A >= 10` is a written instruction, not a
  mechanism — unless you install the optional hook gate (`hooks/`), which enforces the cap, the
  Phase-4-only write ban, the `Working on:` marker, the counter numbering and the secret scan for
  the primary agent. It still cannot see subagent tool calls on Claude Code or OpenCode.
- **The ledger is prose.** `H<n>` and `F<n>` blocks are markdown. The gate requires the markers a
  verdict needs, but a well-formatted block with a hollow probe behind it is still possible; the
  gate cannot tell a real experiment from a plausible narrative.
- **Not benchmarked.** No measurement of whether the skill improves fix quality against a
  baseline. The one recorded run (above) is a demonstration, not a controlled comparison.
- **Assumes a task file exists.** It needs somewhere to write findings. Projects tracked only
  in an external issue tracker need a local file convention first.

These are the honest limits. Each is a candidate for a hook, a script, or a test — some are now
enforced, and the rest are listed rather than implied.

---

## Design notes

**Why five phases and not three.** In 2.1 the fix was written inside the hypothesis phase, so
that phase both guessed and edited — and the shortcuts it took (no "did the test actually fail"
step, no secret scan) were invisible until someone compared the two paths. Now the edit happens
in exactly one phase, so the asymmetric shortcut is impossible by construction.

**Why edges are named.** Two different reasons lead from probe back to research: all hypotheses
refuted (go find new ones), and one hypothesis partially right (deepen that one). Keyed by phase
pair, the second silently overwrote the first, and a partial verdict with a non-empty queue could
not leave Phase 4 at all. The name is the key; the pair is a field.

**Why a baseline in Phase 1.** Phase 5 asks "did I break anything". Without the list of tests
that were already red, that question cannot be answered, and every pre-existing failure
becomes indistinguishable from a regression.

**Why clarification is a precondition, not advice.** A task understood wrongly produces a
working solution to a different problem. The gate cannot judge the answers, but it can require
that the asking happened.

**Why search on evidence, not on the problem statement.** Searching `NullReferenceException at
Foo.bar` finds prior art; searching "the program crashes" does not.

**Why the ledger is the task file.** A separate debug log drifts from the task and is lost
between sessions. Findings belong where the next reader looks first.

**Why the project contract is a separate file.** The five phases are universal; the
commands are not. Baking `make test` into a skill makes it wrong in every project that does
not have a Makefile, and an agent that cannot find the mandated command improvises. A file
read at startup keeps the skill portable and makes the project-specific assumptions
reviewable, instead of invisibly encoded in prose.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it inside a commercial product.
