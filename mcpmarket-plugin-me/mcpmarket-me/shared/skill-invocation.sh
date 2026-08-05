#!/usr/bin/env bash
# MCPmarket skill-invocation telemetry hook.
# Wired as PostToolUse + PostToolUseFailure hooks with matcher "Skill"
# — Claude Code's matcher filters exclusively, so this script only runs
# when the Skill tool is invoked, not on every tool call. Both events
# share this script because the classifier handles either shape;
# without the PostToolUseFailure wiring every failed invocation would
# drop on the floor.
#
# Reads the hook input JSON from stdin. Fires a single fire-and-forget
# POST to /api/v1/plugin/skill-invocation with:
#   - skill_slug   (from tool_input)
#   - source       (user|agent — prefers the native `invocation_trigger`
#                   field on the hook input; falls back on older Claude
#                   Code versions to tailing transcript_path for
#                   <command-name>$slug</command-name>. The prompt text
#                   never leaves the user's machine — only the source
#                   bit travels.)
#   - outcome      (success|error — derived from tool_response shape)
#   - error_class  (closed enum, only when outcome=error)
#
# Reads only:
#   CLAUDE_PLUGIN_ROOT  plugin install dir (Claude Code populates this)
#   MCPMARKET_API_URL   API base URL override (allowlisted, same as sync.sh)
#
# Credentials come from $PLUGIN_ROOT/.mcp.json — same source sync.sh
# uses. No hook-shim wrapping; PostToolUse doesn't get the same env
# plumbing SessionStart does, so we read directly.

set -euo pipefail

MCPMARKET_HOOK_VERSION="0.1.0"
USER_AGENT="mcpmarket-skill-hook/${MCPMARKET_HOOK_VERSION}"

# Split literal so the build-time substitution can't rewrite the
# comparison along with the assignment. Mirrors sync.sh's pattern.
MCPMARKET_CLIENT="claude_desktop"
if [ "$MCPMARKET_CLIENT" = "__MCPMARKET""_CLIENT__" ]; then
  MCPMARKET_CLIENT=""
fi

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${MCPMARKET_PLUGIN_ROOT:-}}"
if [ -z "$PLUGIN_ROOT" ]; then
  SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  PLUGIN_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
fi

# Hard dependencies. Silent exit (not stderr) because hooks fire on
# every skill invocation and a noisy hook would spam the user's terminal.
command -v node >/dev/null 2>&1 || exit 0
command -v curl >/dev/null 2>&1 || exit 0

HOOK_INPUT=$(cat)
[ -n "$HOOK_INPUT" ] || exit 0

# Parse hook input. The tool_response shape isn't formally specified
# across Claude Code versions, so the error detection is permissive:
# any of `is_error`, `error`, or `success === false` flags an error.
# Skill slug source: tool_input.skill (canonical) or tool_input.name.
# All string fields are scrubbed of separator characters (\t/\n/\x00/\x1F)
# so a forged value can't smuggle extra positional fields. \x1F (Unit
# Separator) is used as the inter-field delimiter — see sync.sh for the
# rationale (tab is IFS-whitespace and bash `read` compacts consecutive
# tabs, so an empty middle field would shift later fields left).
FIELDS=$(HOOK_JSON="$HOOK_INPUT" node -e '
  const UNSAFE = /[\t\n\x00\x1F]/;
  const clean = (s) => (typeof s === "string" && !UNSAFE.test(s) ? s : "");
  try {
    const h = JSON.parse(process.env.HOOK_JSON);
    const toolName = clean(h.tool_name);
    const ti = h.tool_input || {};
    const skill = clean(ti.skill || ti.name || "");
    const transcript = clean(h.transcript_path || "");
    // `invocation_trigger` is emitted by Claude Code alongside the
    // `claude_code.skill_activated` OTel event and is the formal source
    // of truth for whether the skill came from a user slash command, an
    // autonomous agent decision, or a nested skill invocation. Older
    // Claude Code versions do not populate it — the bash side falls
    // back to the transcript-tail heuristic when this field is empty.
    const trigger = clean(h.invocation_trigger || "");
    let outcome = "success";
    let errorClass = "";
    // Hook input may carry the failure signal in two places:
    //   - PostToolUse with error: tool_response = { is_error: true, error/message/content: "..." }
    //   - PostToolUseFailure:     top-level `error` string, no tool_response
    // Either is treated as a failure; the message source is whichever is
    // present (tool_response fields preferred when both exist). The
    // classifier vocabulary is identical so analytics queries do not have
    // to split on event source.
    const classify = (raw) => {
      const s = String(raw || "").toLowerCase();
      if (s.includes("not found") || s.includes("unknown skill")) return "not_found";
      if (s.includes("timeout") || s.includes("timed out")) return "timeout";
      if (s) return "runtime_error";
      // Errored but no extractable message — shape drifted; route to
      // `unknown` so the regression surfaces in analytics.
      return "unknown";
    };
    const resp = h.tool_response;
    const topError = typeof h.error === "string" ? h.error : "";
    if (resp && typeof resp === "object") {
      const errored = resp.is_error === true || resp.success === false || !!resp.error;
      if (errored) {
        outcome = "error";
        errorClass = classify(resp.error || resp.message || resp.content || topError);
      }
    } else if (topError) {
      // PostToolUseFailure shape: no tool_response, top-level error string.
      outcome = "error";
      errorClass = classify(topError);
    } else {
      // No tool_response AND no top-level error — either Claude Code
      // crashed before producing any failure signal, or the hook-input
      // shape drifted in a release. Record as error/unknown rather than
      // silently rolling up as success.
      outcome = "error";
      errorClass = "unknown";
    }
    process.stdout.write([toolName, skill, transcript, trigger, outcome, errorClass].join("\x1F"));
  } catch { process.exit(1); }
') || exit 0

IFS=$'\x1F' read -r TOOL_NAME SKILL_SLUG TRANSCRIPT_PATH INVOCATION_TRIGGER OUTCOME ERROR_CLASS <<<"$FIELDS"

# Defence-in-depth: matcher should already restrict to Skill, but a
# misconfigured hooks.json shouldn't let arbitrary tool calls fire
# telemetry under the skill_invocation event type.
[ "$TOOL_NAME" = "Skill" ] || exit 0
[ -n "$SKILL_SLUG" ] || exit 0

# Server-side validator rejects malformed slugs; reject early so we
# don't waste a network round-trip on input we know will 400. Server
# `slugSchema` requires `.min(2)`, so the bash guard enforces the same
# two-character floor — a single-char slug would pass `[a-z0-9]$` but
# the route would 400 it anyway.
if ! echo "$SKILL_SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
  exit 0
fi

# Read credentials + toolkit URL from .mcp.json. Same parser sync.sh
# uses; each plugin's .mcp.json holds exactly one server keyed by the
# per-toolkit plugin name (`mcpmarket-<slug>`), so we read the only key
# under `mcpServers` rather than hardcoding the key. The \x1F separator
# preserves an empty leading field.
[ -f "$PLUGIN_ROOT/.mcp.json" ] || exit 0
CREDS=$(MCP_CONFIG_PATH="$PLUGIN_ROOT/.mcp.json" node -e '
  try {
    const cfg = JSON.parse(require("fs").readFileSync(process.env.MCP_CONFIG_PATH, "utf8"));
    const servers = (cfg && cfg.mcpServers) || {};
    const keys = Object.keys(servers);
    const s = keys.length === 1 ? servers[keys[0]] : {};
    const url = s.url || "";
    const auth = (s.http_headers && s.http_headers.Authorization) || (s.headers && s.headers.Authorization) || "";
    process.stdout.write(url + "\x1F" + auth);
  } catch { process.stdout.write("\x1F"); }
') || exit 0
IFS=$'\x1F' read -r TOOLKIT_URL MCP_AUTH <<<"$CREDS"
API_TOKEN="${MCP_AUTH#Bearer }"

[ -n "$TOOLKIT_URL" ] || exit 0
[ -n "$API_TOKEN" ] || exit 0

# Parse org/toolkit slugs. Same logic and validation as sync.sh — a
# malformed URL exits silently rather than POSTing junk.
URL_PATH=$(echo "$TOOLKIT_URL" | sed -E 's|https?://[^/]*/||; s|/mcp$||')
ORG_SLUG=$(echo "$URL_PATH" | cut -d'/' -f1)
TOOLKIT_SLUG=$(echo "$URL_PATH" | cut -d'/' -f3)

[ -n "$ORG_SLUG" ] && [ -n "$TOOLKIT_SLUG" ] || exit 0
# Two-char floor matches server `slugSchema.min(2)` — a single-char
# slug would pass and then be rejected with a 400.
echo "$ORG_SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$' || exit 0
echo "$TOOLKIT_SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$' || exit 0

# API base URL allowlist — same parser as sync.sh so dev overrides
# (localhost / *.mcpmarket.com) match the success-path's behaviour.
API_BASE_URL="${MCPMARKET_API_URL:-https://app.mcpmarket.com}"
API_SCHEME=""
API_HOST=""
case "$API_BASE_URL" in
  https://*) API_SCHEME=https; API_HOST="${API_BASE_URL#https://}"; API_HOST="${API_HOST%%/*}" ;;
  http://*)  API_SCHEME=http;  API_HOST="${API_BASE_URL#http://}";  API_HOST="${API_HOST%%/*}" ;;
esac
case "$API_HOST" in
  *[!a-zA-Z0-9.:-]*) API_HOST="" ;;
  *:*:*)             API_HOST="" ;;
esac
API_ALLOWED=false
if [ "$API_SCHEME" = "https" ]; then
  case "$API_HOST" in
    app.mcpmarket.com|*.mcpmarket.com) API_ALLOWED=true ;;
  esac
elif [ "$API_SCHEME" = "http" ]; then
  case "$API_HOST" in
    localhost|localhost:*|127.0.0.1|127.0.0.1:*) API_ALLOWED=true ;;
  esac
fi
[ "$API_ALLOWED" = "true" ] || exit 0

# Source attribution. Prefer Claude Code's native `invocation_trigger`
# field (emitted alongside the `claude_code.skill_activated` OTel event)
# when present — it's the formal source of truth and avoids the
# transcript-tail heuristic's misattribution of the second-and-onward
# agent re-invocation of the same skill in one user turn. On older
# Claude Code versions the field is absent; fall back to tailing the
# JSONL transcript for a `<command-name>$SKILL_SLUG` tag in the most
# recent user message. The prompt text is consumed locally and
# discarded — only the source bit travels.
#
# The trigger string literals below are a Claude Code wire-format
# contract. The same three values appear in two test sites that lock
# the mapping; keep all three in sync when Claude Code adds a new
# trigger value:
#   - apps/web/lib/__tests__/plugin-source.test.ts (case-arm regex)
#   - apps/web/lib/plugin-template/__tests__/skill-invocation.test.ts (it.each table)
SOURCE="agent"
case "$INVOCATION_TRIGGER" in
  user-slash)
    SOURCE="user"
    ;;
  claude-proactive|nested-skill)
    SOURCE="agent"
    ;;
  *)
    if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
      LAST_USER=$(tail -n 50 "$TRANSCRIPT_PATH" 2>/dev/null | TARGET_SLUG="$SKILL_SLUG" node -e '
        const target = process.env.TARGET_SLUG;
        const lines = require("fs").readFileSync(0, "utf8").split("\n");
        for (let i = lines.length - 1; i >= 0; i--) {
          const line = lines[i].trim();
          if (!line) continue;
          let obj;
          try { obj = JSON.parse(line); } catch { continue; }
          const role = obj.role || (obj.message && obj.message.role) || obj.type;
          if (role !== "user") continue;
          const content = obj.message ? obj.message.content : obj.content;
          const text = typeof content === "string" ? content : JSON.stringify(content || "");
          if (text.includes("<command-name>" + target + "</command-name>")) {
            process.stdout.write("user");
          }
          process.exit(0);
        }
      ' 2>/dev/null || true)
      if [ "$LAST_USER" = "user" ]; then
        SOURCE="user"
      fi
    fi
    ;;
esac

# Build JSON payload. Inline interpolation is safe: every field above
# has been regex-validated (skill/org/toolkit slugs) or is sourced from
# a closed enum (source, outcome, error_class, client).
INVOCATION_URL="${API_BASE_URL}/api/v1/plugin/skill-invocation"

# Build a single body via node to avoid bash-printf escape pitfalls
# when conditionally including errorClass and client.
PAYLOAD=$(SKILL="$SKILL_SLUG" ORG="$ORG_SLUG" TK="$TOOLKIT_SLUG" \
  SRC="$SOURCE" OUT="$OUTCOME" ERR="$ERROR_CLASS" CLIENT="$MCPMARKET_CLIENT" \
  node -e '
    const body = {
      skillSlug: process.env.SKILL,
      orgSlug: process.env.ORG,
      toolkitSlug: process.env.TK,
      source: process.env.SRC,
      outcome: process.env.OUT,
    };
    if (process.env.OUT === "error" && process.env.ERR) body.errorClass = process.env.ERR;
    if (process.env.CLIENT) body.client = process.env.CLIENT;
    process.stdout.write(JSON.stringify(body));
  ') || exit 0

CLIENT_HEADER_ARGS=()
if [ -n "$MCPMARKET_CLIENT" ]; then
  CLIENT_HEADER_ARGS=(-H "X-MCPmarket-Client: $MCPMARKET_CLIENT")
fi

curl -sS --max-time 3 -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "User-Agent: $USER_AGENT" \
  ${CLIENT_HEADER_ARGS[@]+"${CLIENT_HEADER_ARGS[@]}"} \
  -d "$PAYLOAD" \
  "$INVOCATION_URL" >/dev/null 2>&1 || true

exit 0
