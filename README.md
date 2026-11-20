# bug-fixer

A three-phase debugging skill for coding agents. It stops the agent from writing a fix
before the cause is known.

```
Phase 1 (reproduce) --> Phase 2 (hypothesise & probe) --> Phase 3 (decide)
     ^                                                        |
     |                       degradation (N < 10) ------------+
     |                                        |
     +----------------------------------------+
                        N >= 10 --> BLOCKED (ask the user)
                        success --> DONE
```

- **Phase 1** builds a failing test, an e2e test, and a full-suite baseline. No production
  code changes are allowed yet.
- **Phase 2** ranks at least two hypotheses, and each one gets a *discriminating* experiment
  plus targeted logging. A fix is written only after the cause is confirmed by evidence.
- **Phase 3** reruns everything. Four conditions must hold at once: the RED test passes, the
  e2e test passes, no baseline-green test turned red, and debug logging is gone. Otherwise
  the fix is reverted and the loop returns to Phase 1 with new information.

Two counters, kept separate on purpose: `N` counts complete passes and is capped at 10;
`M` counts experiments inside Phase 2 and is unlimited but logged. Conflating them lets an
agent burn its iteration budget on half-hypotheses, or the reverse.

Findings go into the **target task file** as `F<n>` blocks. This skill does not create a
`DEBUG.md`; one ledger, not two.

### It is project-agnostic, and it adapts before it works

The three phases are the same everywhere. The commands are not: a Rust crate runs
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

---

## What is in here

```
bug-fixer/
├── SKILL.md                      # the skill: phases, counters, prohibitions
├── LICENSE                       # MIT
├── references/
│   ├── workflow.md               # how to build a discriminator, anti-patterns
│   ├── project-contract.md       # TEMPLATE: fill this for your project
│   └── repo-contract.md          # filled example: linux_to_mac only
└── templates/
    └── finding-block.md          # F<n> block to append to the task file
```

`SKILL.md` is the only file loaded up front. References load when the skill needs them.

---

## Before you install: what the agent must be able to do

The skill is instructions, not code. It can do nothing the agent cannot already do.

| Capability | Needed for | If missing |
|---|---|---|
| Edit files in the repo | writing tests, logging, the ledger | skill cannot run; nothing to fall back on |
| Run the project's test command | Phase 1 baseline, Phase 3 gate | blocked at Phase 1: no baseline means no regression check |
| Write to the ledger path | recording findings | skill stops and asks for a path |
| A search tool, if the contract allows one | Phase 2.1 only | Phase 2.1 is skipped; hypotheses come from Phase 1 evidence |

A read-only agent can reproduce and hypothesise, but it cannot complete Phase 3, because
that phase requires writing a fix and rerunning the suite.

---

## Install

The format is the same everywhere: a folder named `bug-fixer` containing `SKILL.md`.
Pick the destination for your agent, then restart the session (or start a new chat) so the
skill metadata is re-read.

> The clone below uses HTTPS so it works without an SSH key. Replace it with
> `git@github.com:Dimon70007/DG-Bug-fixer.git` if you already have a key set up.

### Cursor

| Scope | Path |
|---|---|
| Project | `.cursor/skills/bug-fixer/` |
| Every project | `~/.cursor/skills/bug-fixer/` |

```bash
git clone https://github.com/Dimon70007/DG-Bug-fixer.git /tmp/dg-bug-fixer

# every project
mkdir -p ~/.cursor/skills && cp -R /tmp/dg-bug-fixer ~/.cursor/skills/bug-fixer

# or one project
mkdir -p .cursor/skills && cp -R /tmp/dg-bug-fixer .cursor/skills/bug-fixer
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
mkdir -p ~/.claude/skills && cp -R /tmp/dg-bug-fixer ~/.claude/skills/bug-fixer
```

### Codex

Official locations, per OpenAI's skill docs:

| Scope | Path |
|---|---|
| Project | `.agents/skills/bug-fixer/` (also scanned in every directory up to the repo root) |
| Every project | `$HOME/.agents/skills/bug-fixer/` |
| Admin / machine-wide | `/etc/codex/skills/bug-fixer/` |

```bash
mkdir -p ~/.agents/skills && cp -R /tmp/dg-bug-fixer ~/.agents/skills/bug-fixer
```

Invoke with `$bug-fixer` or `/skills` to browse. Older guides mention `~/.codex/skills/`;
OpenAI's current documentation lists `.agents/skills` for repo and user scope.

### OpenCode

| Scope | Path |
|---|---|
| Project | `.opencode/skills/bug-fixer/` |
| Every project | `~/.config/opencode/skills/bug-fixer/` |

```bash
mkdir -p ~/.config/opencode/skills && cp -R /tmp/dg-bug-fixer ~/.config/opencode/skills/bug-fixer
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
mkdir -p ~/workspace/skills && cp -R /tmp/dg-bug-fixer ~/workspace/skills/bug-fixer
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
same observation and neither could be killed. Full blocks:
[`tasks/TASK-078`](../..) in the `linux_to_mac` repository.

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

## Optional: enforce the loop with hooks

The rules above are text. An optional gate turns three of them into mechanisms: the iteration
cap, the Phase-1 write ban, and "no writes after `BLOCKED`/`DONE`".

```
hooks/
├── bugfixer_gate.py         # the decision (reads a harness payload on stdin)
├── bugfixer_gate_state.py   # phase/status sidecar the skill advances
├── cursor.hooks.json        # template -> .cursor/hooks.json
├── claude.settings.json     # template -> .claude/settings.json
├── codex.hooks.json         # template -> .codex/hooks.json
├── opencode-plugin.ts       # -> .opencode/plugin/bugfixer-gate.ts
└── openclaw-plugin.ts       # OpenClaw plugin entry
```

### How strong is it, per harness

| Harness | Mechanism | Blocks the primary agent | Blocks subagents |
|---|---|---|---|
| Cursor | `preToolUse` + `failClosed: true` | yes | **no** — user hooks are absent in cloud agents |
| Claude Code | `PreToolUse` + `onFailure: block` | yes | **no** — see [#40580](https://github.com/anthropics/claude-code/issues/40580) |
| Codex | `PreToolUse` | yes | not documented |
| OpenCode | `tool.execute.before` → `throw` | yes | **no** — see [#5894](https://github.com/anomalyco/opencode/issues/5894) |
| OpenClaw | `before_tool_call` → `{ block: true }` | yes | inherits the plugin |

Read that table before trusting it. The gate is **strong for the primary agent** and
**advisory wherever a subagent can be spawned**. Claiming otherwise would be false.

### Two footguns the templates already handle

- **Claude Code exit `1` blocks nothing.** A crashing guard looks like permission. The template
  sets `onFailure: "block"`.
- **Cursor fails open by default** on crashes, timeouts, and non-`2` exits. The template sets
  `failClosed: true`.

### What the gate decides

| Condition | Writes denied |
|---|---|
| `STATUS: BLOCKED` or `STATUS: DONE` in the ledger | all |
| 10 iterations recorded and no terminal status | all |
| Phase 1, target outside the contract's `Test paths:` | that write |
| anything unreadable (corrupt sidecar, no `Test paths:`) | all, with the reason |

`N` is counted from the `## Iteration N` headings in the ledger. A hook sees tool calls, not
loops, so the ledger is the only honest source. The phase cannot be inferred at all, so it lives
in a sidecar the skill advances:

```bash
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md phase 2
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md status BLOCKED
python3 hooks/bugfixer_gate_state.py tasks/TASK-123.md
```

Unknown phase fails closed to Phase 1: only test paths may be written.

### Not enforced

- **Revert on degradation.** The gate cannot tell a revert from a rewrite.
- **Subagent bypass.** Not fixable from a hook; documented above.
- **"Is this write really needed".** The gate checks paths and status, not intent.

---

## Limitations, stated plainly

- **No enforcement.** The skill is text. Nothing mechanically prevents an agent from editing
  production code in Phase 1. The prohibition is written as a hard rule in `SKILL.md` and
  repeated in the contract, which improves compliance; it does not guarantee it.
- **No iteration counter in code.** `N >= 10` is a written instruction, not a mechanism — unless
  you install the optional hook gate (`hooks/`), which makes the cap and the Phase-1 write ban
  enforced for the primary agent. It still cannot see subagent tool calls on Claude Code or
  OpenCode.
- **The ledger is prose.** `F<n>` blocks are markdown. Nothing validates that a block has an
  experiment, and a careless run can produce a well-formatted block with no evidence behind it.
- **Not benchmarked.** No measurement of whether the skill improves fix quality against a
  baseline. The one recorded run (above) is a demonstration, not a controlled comparison.
- **Assumes a task file exists.** It needs somewhere to write findings. Projects tracked only
  in an external issue tracker need a local file convention first.

These are the honest limits. Each is a candidate for a hook, a script, or a test — none is
in place today.

---

## Design notes

**Why a state machine and not a checklist.** Without an explicit edge back to Phase 1, agents
either abandon a bug after one failed fix or keep polishing a wrong hypothesis. The edge is
named `BACK_TO_PHASE_1` and requires new information to justify it.

**Why a baseline in Phase 1.** Phase 3 asks "did I break anything". Without the list of tests
that were already red, that question cannot be answered, and every pre-existing failure
becomes indistinguishable from a regression.

**Why search on evidence, not on the problem statement.** Searching `NullReferenceException at
Foo.bar` finds prior art; searching "the program crashes" does not.

**Why the ledger is the task file.** A separate debug log drifts from the task and is lost
between sessions. Findings belong where the next reader looks first.

**Why the project contract is a separate file.** The three phases are universal; the
commands are not. Baking `make test` into a skill makes it wrong in every project that does
not have a Makefile, and an agent that cannot find the mandated command improvises. A file
read at startup keeps the skill portable and makes the project-specific assumptions
reviewable, instead of invisibly encoded in prose.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it inside a commercial product.
