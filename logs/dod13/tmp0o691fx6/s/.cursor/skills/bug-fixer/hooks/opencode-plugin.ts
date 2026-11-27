/**
 * bug-fixer gate for OpenCode (TASK-079 / TASK-079.2 F1).
 *
 * OpenCode intercepts tool calls in-process, so this plugin cannot use the
 * stdin/exit-code contract of the shell harnesses. It spawns the same Python
 * gate and throws on a deny verdict, which is how an OpenCode plugin blocks a
 * call.
 *
 * Install: copy to `.opencode/plugin/bugfixer-gate.ts` (project) or
 * `~/.config/opencode/plugins/bugfixer-gate.ts` (global). No ledger path is
 * configured here: the gate finds it from the project contract (``Task/bug file:``)
 * in the tool call's working directory, so the same file works in every project.
 *
 * Runtime: `node:child_process`, not `Bun.spawn`. The plugin host is not
 * guaranteed to expose a `Bun` global, and a ReferenceError inside a gate reads
 * to the agent as "the guard is broken", which is the one failure mode that
 * must never be ambiguous.
 *
 * No session hook here: OpenCode fires `tool.execute.before` and nothing that injects text into the
 * session, so registering one would be an event the host ignores — a hook that looks wired and is
 * silent, which §0 calls the worst failure. The project rule in `AGENTS.md` (or `CLAUDE.md`, which
 * OpenCode falls back to) carries the initialisation instruction instead (spec §18.3, invariant 41).
 *
 * KNOWN LIMITATION: OpenCode does not fire `tool.execute.before` for subagent
 * tool calls (anomalyco/opencode#5894). A subagent can therefore act despite a
 * deny here. This plugin enforces the loop for the primary agent only.
 */

import { spawn } from "node:child_process"
import type { Plugin } from "@opencode-ai/plugin"

const PYTHON = "__PYTHON__"
const GATE = "__GATE__"

interface GateResult {
  allow: boolean
  reason: string
}

/**
 * Run the shared Python gate for one tool call.
 *
 * A non-zero exit is a deny: exit 2 is the gate's own verdict, and any other
 * failure means the gate could not decide, which must not read as permission.
 *
 * @param tool - OpenCode tool name, e.g. "write", "edit", "bash".
 * @param args - Tool arguments as OpenCode resolved them.
 * @param cwd - Project directory; the gate searches it for the contract and ledger.
 * @returns The gate verdict and, on deny, the reason to show the agent.
 */
function runGate(
  tool: string,
  args: Record<string, unknown>,
  cwd: string | undefined,
): Promise<GateResult> {
  const payload = JSON.stringify({ tool_name: tool, tool_input: args, cwd })

  return new Promise<GateResult>((resolve) => {
    let stdout = ""
    let settled = false

    const finish = (result: GateResult): void => {
      if (settled) return
      settled = true
      resolve(result)
    }

    const child = spawn(PYTHON, [GATE], {
      stdio: ["pipe", "pipe", "pipe"],
    })

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8")
    })

    child.on("error", (error: Error) => {
      finish({
        allow: false,
        reason:
          `bug-fixer gate could not start (${error.message}). Do not edit code. ` +
          "Report the failure to the user and stop.",
      })
    })

    child.on("close", (code: number | null) => {
      if (code === 0) {
        finish({ allow: true, reason: "" })
        return
      }

      try {
        const parsed = JSON.parse(stdout) as { agent_message?: string }
        if (parsed.agent_message) {
          finish({ allow: false, reason: parsed.agent_message })
          return
        }
      } catch {
        /* fall through to the generic reason */
      }

      finish({
        allow: false,
        reason:
          `bug-fixer gate failed (exit ${code}). Do not edit code. ` +
          "Report the failure to the user and stop.",
      })
    })

    child.stdin.end(payload, "utf8")
  })
}

export const BugFixerGate: Plugin = async () => {
  return {
    "tool.execute.before": async (input, output) => {
      const cwd =
        (typeof input.cwd === "string" && input.cwd) ||
        (typeof input.directory === "string" && input.directory) ||
        process.cwd()
      const verdict = await runGate(input.tool, output.args ?? {}, cwd)
      if (!verdict.allow) throw new Error(verdict.reason)
    },
  }
}
