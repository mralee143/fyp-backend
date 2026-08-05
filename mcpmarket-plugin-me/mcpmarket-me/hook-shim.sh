#!/usr/bin/env bash
# Maps Claude Code env vars onto the agent-neutral MCPMARKET_* contract,
# then runs shared/sync.sh.
set -euo pipefail
export MCPMARKET_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:?}"
export MCPMARKET_TOKEN="${CLAUDE_PLUGIN_OPTION_api_token:-}"
export MCPMARKET_TOOLKIT_URL="${CLAUDE_PLUGIN_OPTION_toolkit_url:-}"
export MCPMARKET_API_URL="${CLAUDE_PLUGIN_OPTION_api_url:-}"
exec bash "${MCPMARKET_PLUGIN_ROOT}/shared/sync.sh" "$@"
