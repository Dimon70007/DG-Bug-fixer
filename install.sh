#!/usr/bin/env bash
# Install this skill into agent environments.
#
# Usage:
#   ./install.sh --target cursor
#   ./install.sh --target all --dry-run
#   ./install.sh --target home --home ~               # explicit path is mandatory
#   ./install.sh --target personal                    # ~/.cursor/skills, every project
#   ./install.sh --target openclaw --dest ~/workspace
#   ./install.sh --target cursor --project /path/to/repo
#   ./install.sh --target cursor --project /path/to/repo --uninstall
#
# Logic lives in install.py (repo rule: no inline Python in bash).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PY="${SCRIPT_DIR}/install.py"

if [[ ! -f "${INSTALL_PY}" ]]; then
  echo "Missing installer: ${INSTALL_PY}" >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 --target <cursor|codex|claude|opencode|personal*|openclaw|all> [--dest PATH] [--project DIR] [--dry-run]" >&2
  exit 2
fi

exec python3 "${INSTALL_PY}" "$@"
