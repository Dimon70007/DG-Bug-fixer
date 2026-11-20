/**
 * bug-fixer gate for OpenClaw (TASK-079).
 *
 * OpenClaw exposes typed in-process hooks. `before_tool_call` may return
 * `{ block: true, blockReason }`, which is the documented way for a plugin to
 * stop a tool call; `api.registerHook` with the same name is the wrong API and
 * is never invoked for typed lifecycle names.
 *
 * Install: register this plugin with OpenClaw, then set the ledger path.
 */

import { definePluginEntry, type OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"

const PYTHON = "__PYTHON__"
const GATE = "__GATE__"
const LEDGER = "__LEDGER__"

/**
 * Ask the shared Python gate whether a tool call is allowed.
 *
 * @param toolName - OpenClaw tool name, e.g. "exec", "write".
 * @param params - Tool parameters as resolved by OpenClaw.
 * @returns The block decision and the reason to hand back to the agent.
 */
async function askGate(
  toolName: string,
  params: Record<string, unknown>,
): Promise<{ block: boolean; blockReason?: string }> {
  const payload = JSON.stringify({ tool_name: toolName, tool_input: params })
  const proc = Bun.spawn([PYTHON, GATE, "--ledger", LEDGER], {
    stdin: new TextEncoder().encode(payload),
    stdout: "pipe",
    stderr: "pipe",
  })

  const code = await proc.exited
  const stdout = await new Response(proc.stdout).text()

  if (code === 0) return { block: false }

  try {
    const parsed = JSON.parse(stdout) as { agent_message?: string }
    if (parsed.agent_message) return { block: true, blockReason: parsed.agent_message }
  } catch {
    /* fall through */
  }

  return {
    block: true,
    blockReason:
      `bug-fixer gate failed (exit ${code}). Do not edit code. ` +
      "Report the failure to the user and stop.",
  }
}

export default definePluginEntry({
  id: "bugfixer-gate",
  register(api: OpenClawPluginApi) {
    api.on(
      "before_tool_call",
      async (event: { toolName: string; params?: Record<string, unknown> }) =>
        askGate(event.toolName, event.params ?? {}),
      // Matcher narrows the hook to tools that can mutate the filesystem. A
      // gate that fires on every read adds latency for no benefit.
      { matcher: ["exec", "write", "edit", "apply_patch"], priority: 50 },
    )
  },
})
