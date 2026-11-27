#!/usr/bin/env bash
# Cursor preToolUse entry point for the bug-fixer gate.
#
# Why a wrapper and not the Python command directly: a plugin hook cannot embed an
# absolute interpreter path, because the path differs on every machine. Cursor runs
# plugin hooks with the plugin directory as their working directory for non-stop
# hooks, so a relative `./hooks/...` is what resolves. `python3` comes from PATH.
#
# The gate reads the harness payload on stdin and finds the ledger from the project
# contract, so nothing here is machine-specific. Arguments are forwarded: `--dev-plugin`
# is the documented way to develop the plugin with its own gate installed, and a wrapper
# that drops flags leaves the hook with no way to pass it.
#
# Exit code 2 denies the tool call; any other non-zero code would fail open, which is
# why `set -e` is deliberately absent and the exit code is passed through verbatim.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/bugfixer_gate.py" "$@"
