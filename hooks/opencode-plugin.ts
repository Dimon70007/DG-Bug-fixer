/**
 * bug-fixer gate for OpenCode (TASK-079).
 *
 * OpenCode intercepts tool calls in-process, so this plugin cannot use the
 * stdin/exit-code contract of the shell harnesses. It spawns the same Python
 * gate and throws on a deny verdict, which is how an OpenCode plugin blocks a
 * call.
 *
 * Install: copy to `.opencode/plugin/bugfixer-gate.ts` (project) or
 * `~/.config/opencode/plugins/bugfixer-gate.ts` (global), then set the ledger.
 *
 * KNOWN LIMITATION: OpenCode does not fire `tool.execute.before` for subagent
 * tool calls (anomalyco/opencode#5894). A subagent can therefore act despite a
 * deny here. This plugin enforces the loop for the primary agent only.
 */

import type { Plugin } from "@opencode-ai/plugin"

const PYTHON = "__PYTHON__"
const GATE = "__GATE__"
const LEDGER = "__LEDGER__"

interface GateResult {
  allow: boolean
  reason: string
}

/**
 * Run the shared Python gate for one tool call.
 *
 * @param tool - OpenCode tool name, e.g. "write", "edit", "bash".
 * @param args - Tool arguments as OpenCode resolved them.
 * @returns The gate verdict and, on deny, the reason to show the agent.
 */
async function runGate(tool: string, args: Record<string, unknown>): Promise<GateResult> {
  const payload = JSON.stringify({ tool_name: tool, tool_input: args })

  const proc = Bun.spawn([PYTHON, GATE, "--ledger", LEDGER], {
    stdin: new TextEncoder().encode(payload),
    stdout: "pipe",
    stderr: "pipe",
  })

  const code = await proc.exited
  const stdout = await new Response(proc.stdout).text()

  // Exit 2 is the gate's deny. Anything else non-zero means the gate itself
  // broke; deny too, because a guard that fails open is not a guard.
  if (code !== 0) {
    try {
      const parsed = JSON.parse(stdout) as { agent_message?: string }
      if (parsed.agent_message) return { allow: false, reason: parsed.agent_message }
    } catch {
      /* fall through to the generic reason */
    }
    return {
      allow: false,
      reason:
        `bug-fixer gate failed (exit ${code}). Do not edit code. ` +
        "Report the failure to the user and stop.",
    }
  }

  return { allow: true, reason: "" }
}

export const BugFixerGate: Plugin = async () => {
  return {
    "tool.execute.before": async (input, output) => {
      const verdict = await runGate(input.tool, output.args ?? {})
      if (!verdict.allow) throw new Error(verdict.reason)
    },
  }
}
