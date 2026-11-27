/**
 * bug-fixer gate for OpenClaw (TASK-079 / TASK-079.2 F1).
 *
 * OpenClaw exposes typed in-process hooks. `before_tool_call` may return
 * `{ block: true, blockReason }`, which is the documented way for a plugin to
 * stop a tool call; `api.registerHook` with the same name is the wrong API and
 * is never invoked for typed lifecycle names.
 *
 * Runtime: `node:child_process`, not `Bun.spawn`. OpenClaw is a Node program, so
 * a `Bun` global is not available and a ReferenceError inside a gate reads to
 * the agent as "the guard is broken".
 *
 * Install: register this plugin with OpenClaw. No ledger path is configured here:
 * the gate finds it from the project contract (``Task/bug file:``) in the tool
 * call's working directory, so the same file works in every project.
 *
 * No session hook here: OpenClaw's `session_start` is observe-only and cannot inject context, so
 * registering one would look wired and deliver nothing — the failure §0 calls the worst kind. The
 * project rule in `AGENTS.md`, injected into OpenClaw's Project Context, carries the initialisation
 * instruction instead (spec §18.3, invariant 41).
 */

import { spawn } from "node:child_process"
import { definePluginEntry, type OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry"

const PYTHON = "__PYTHON__"
const GATE = "__GATE__"

interface BlockDecision {
  block: boolean
  blockReason?: string
}

/**
 * Ask the shared Python gate whether a tool call is allowed.
 *
 * @param toolName - OpenClaw tool name, e.g. "exec", "write".
 * @param params - Tool parameters as resolved by OpenClaw.
 * @param cwd - Project directory; the gate searches it for the contract and ledger.
 * @returns The block decision and the reason to hand back to the agent.
 */
function askGate(
  toolName: string,
  params: Record<string, unknown>,
  cwd: string | undefined,
): Promise<BlockDecision> {
  const payload = JSON.stringify({ tool_name: toolName, tool_input: params, cwd })

  return new Promise<BlockDecision>((resolve) => {
    let stdout = ""
    let settled = false

    const finish = (decision: BlockDecision): void => {
      if (settled) return
      settled = true
      resolve(decision)
    }

    const child = spawn(PYTHON, [GATE], {
      stdio: ["pipe", "pipe", "pipe"],
    })

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8")
    })

    child.on("error", (error: Error) => {
      finish({
        block: true,
        blockReason:
          `bug-fixer gate could not start (${error.message}). Do not edit code. ` +
          "Report the failure to the user and stop.",
      })
    })

    child.on("close", (code: number | null) => {
      if (code === 0) {
        finish({ block: false })
        return
      }

      try {
        const parsed = JSON.parse(stdout) as { agent_message?: string }
        if (parsed.agent_message) {
          finish({ block: true, blockReason: parsed.agent_message })
          return
        }
      } catch {
        /* fall through */
      }

      finish({
        block: true,
        blockReason:
          `bug-fixer gate failed (exit ${code}). Do not edit code. ` +
          "Report the failure to the user and stop.",
      })
    })

    child.stdin.end(payload, "utf8")
  })
}

export default definePluginEntry({
  id: "bugfixer-gate",
  register(api: OpenClawPluginApi) {
    api.on(
      "before_tool_call",
      async (event: {
        toolName: string
        params?: Record<string, unknown>
        cwd?: string
        workdir?: string
      }) =>
        askGate(event.toolName, event.params ?? {}, event.cwd ?? event.workdir),
      // Matcher narrows the hook to tools that can mutate the filesystem. A
      // gate that fires on every read adds latency for no benefit.
      { matcher: ["exec", "write", "edit", "apply_patch"], priority: 50 },
    )
  },
})
