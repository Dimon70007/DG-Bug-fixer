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

---

## What is in here

```
bug-fixer/
├── SKILL.md                      # the skill: phases, counters, prohibitions
├── references/
│   ├── workflow.md               # how to build a discriminator, anti-patterns
│   └── repo-contract.md          # ledger location, allowed tools, test entry points
└── templates/
    └── finding-block.md          # F<n> block to append to the task file
```

`SKILL.md` is 278 lines. The other files load only when the agent needs them.

---

## Install

The format is the same everywhere: a folder named `bug-fixer` containing `SKILL.md`.
Pick the destination for your agent, then restart the session (or start a new chat) so the
skill metadata is re-read.

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

The skill will not touch production code until an experiment confirms the cause. That
prohibition is the point: a fix without a confirmed cause is a guess that happens to pass
one test.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Skill never appears | Wrong path, or `skill.md` in lowercase — the filename is case-sensitive |
| Appears after a restart only | Metadata is read at session start; that is expected |
| Not triggered automatically | The `description` must name the situation; vague descriptions are ignored |
| Duplicate name warnings | Two skills share a `name`; only one wins |
| OpenClaw cannot see it | Run `openclaw skills list`, then `openclaw skills info bug-fixer` |

Frontmatter must use spaces, not tabs, and must carry `name` and `description`. The directory
name should match `name`.

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

---

## License

No license file yet. Treat as all rights reserved until one is added.
