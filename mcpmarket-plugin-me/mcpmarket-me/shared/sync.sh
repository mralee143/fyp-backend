#!/usr/bin/env bash
# MCPmarket baseline skill sync (agent-neutral).
# Run by an agent's SessionStart hook (via the agent's hook-shim).
#
# Reads only:
#   MCPMARKET_PLUGIN_ROOT  (required) plugin install dir
#   MCPMARKET_TOKEN        bearer token; falls back to .mcp.json
#   MCPMARKET_TOOLKIT_URL  toolkit MCP URL; falls back to .mcp.json
#   MCPMARKET_API_URL      API base URL override (allowlisted)
#   MCPMARKET_SKILLS_DIR   skills dir; default $PLUGIN_ROOT/skills

set -euo pipefail

MCPMARKET_SYNC_VERSION="0.7.0"
USER_AGENT="mcpmarket-sync/${MCPMARKET_SYNC_VERSION}"

# TTL (seconds): how long after the last successful sync we skip
# re-syncing. Catalog updates aren't time-critical — agents that fire
# SessionStart back-to-back (e.g. fast resume, multiple worktrees)
# should not hammer the baseline endpoint. 5 minutes is short enough
# that interactive testing isn't blocked, long enough to absorb the
# common case.
#
# Env-overridable so internal smoke tests can set TTL=0 to force every
# invocation through the real fetch path. Not documented as a user-
# facing escape hatch — production users get the 5-minute default and
# should re-publish the toolkit if they need a faster propagation than
# that.
MCPMARKET_SYNC_TTL_SECONDS="${MCPMARKET_SYNC_TTL_SECONDS:-300}"

# Reject non-numeric env values rather than crashing under `set -e` at
# the first arithmetic comparison. A user who sets
# `MCPMARKET_SYNC_TTL_SECONDS=foo` in their shell rc would otherwise see
# `integer expression expected` from `[ -lt "foo" ]` and the script
# would exit 1 silently — no telemetry, no skills synced, no error.
# Fall back to the documented default so the sync still proceeds.
case "$MCPMARKET_SYNC_TTL_SECONDS" in
  ''|*[!0-9]*) MCPMARKET_SYNC_TTL_SECONDS=300 ;;
esac

# Split literal so the build-time substitution can't rewrite the
# comparison along with the assignment.
MCPMARKET_CLIENT="claude_desktop"
if [ "$MCPMARKET_CLIENT" = "__MCPMARKET""_CLIENT__" ]; then
  MCPMARKET_CLIENT=""
fi

PLUGIN_ROOT="${MCPMARKET_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
  SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  PLUGIN_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
fi

# TTL gate: short-circuit when the last fully-successful sync is
# fresher than MCPMARKET_SYNC_TTL_SECONDS. The sentinel file is touched
# only at the very end of this script, AFTER the sync_applied POST —
# so any failure path (network error, HTTP non-200, invalid response,
# write loop crash) leaves the sentinel stale and the next run will
# retry immediately. This intentionally has no environment override:
# 5 minutes is short enough that interactive testing isn't blocked.
SYNC_SENTINEL="$PLUGIN_ROOT/.last-sync"
if [ -f "$SYNC_SENTINEL" ]; then
  # GNU `stat -c %Y`, macOS `stat -f %m`. GNU first because BSD stat
  # errors cleanly on `-c` (`stat: illegal option -- c`) and falls
  # through to the next form. The reverse order is BROKEN on Linux:
  # GNU `stat -f` flips to filesystem-status mode and treats the next
  # arg (`%m`) as a filename, then prints the multiline FS-status block
  # for the sentinel — output like `  File: "/path"` lands in
  # _SENTINEL_MTIME, the arithmetic below tries to use it, and under
  # `set -u` the bare identifier `File` fires an unbound-variable
  # crash that kills every sync on Linux. If both forms fail the
  # sentinel age is unknowable and we proceed with the sync rather
  # than risking a stuck cache.
  _SENTINEL_MTIME=$(stat -c %Y "$SYNC_SENTINEL" 2>/dev/null \
    || stat -f %m "$SYNC_SENTINEL" 2>/dev/null \
    || echo 0)
  # Defense-in-depth: any non-numeric output (exotic stat, future
  # refactor that drops the chain) gets coerced to 0 so the arithmetic
  # never crashes. Bare echo-fallthrough already guarantees a 0; this
  # catches the case where a stat call succeeds with garbage.
  case "$_SENTINEL_MTIME" in
    ''|*[!0-9]*) _SENTINEL_MTIME=0 ;;
  esac
  _NOW=$(date +%s)
  _AGE=$((_NOW - _SENTINEL_MTIME))
  # Negative `_AGE` means the sentinel's mtime is in the future — most
  # commonly a backward system-clock step (NTP correction, container
  # restart, VM snapshot). Without the `>= 0` guard, `[ <negative> -lt
  # 300 ]` is true and the gate suppresses every sync until the clock
  # catches up — potentially years. Treat any negative age as cache
  # invalidation and proceed to the full sync; on success the sentinel
  # gets retouched with the current clock.
  if [ "$_SENTINEL_MTIME" -gt 0 ] \
     && [ "$_AGE" -ge 0 ] \
     && [ "$_AGE" -lt "$MCPMARKET_SYNC_TTL_SECONDS" ]; then
    # Cache hit: emit a machine-readable line so agents (notably the
    # bundled `/sync` skill) can distinguish a short-circuit from a
    # silent failure. Stays on stdout so the SessionStart hook surfaces
    # it the same way it surfaces the success line.
    _REMAINING=$((MCPMARKET_SYNC_TTL_SECONDS - _AGE))
    echo "MCPmarket sync: cached (${_REMAINING}s remaining)"
    exit 0
  fi
fi

if ! command -v node &>/dev/null; then
  echo "MCPmarket sync: node not installed — skipping sync" >&2
  exit 0
fi

if [ -n "$PLUGIN_ROOT" ] && [ -f "$PLUGIN_ROOT/.mcp.json" ]; then
  MCP_CONFIG="$PLUGIN_ROOT/.mcp.json"
  # Read the single MCP server entry — each plugin's .mcp.json holds
  # exactly one server, keyed by the per-toolkit plugin name
  # (`mcpmarket-<slug>`). Reading the only key under `mcpServers`
  # avoids threading the slug into this script. Codex uses
  # `http_headers`; Claude uses `headers`. Try both. Use \x1F (Unit
  # Separator, non-whitespace) so an empty leading field is preserved
  # — tab would be stripped as IFS whitespace.
  MCP_FIELDS=$(MCP_CONFIG_PATH="$MCP_CONFIG" node -e '
    try {
      const cfg = JSON.parse(require("fs").readFileSync(process.env.MCP_CONFIG_PATH, "utf8"));
      const servers = (cfg && cfg.mcpServers) || {};
      const keys = Object.keys(servers);
      const s = keys.length === 1 ? servers[keys[0]] : {};
      const url = s.url || "";
      const auth = (s.http_headers && s.http_headers.Authorization) || (s.headers && s.headers.Authorization) || "";
      process.stdout.write(url + "\x1F" + auth);
    } catch { process.stdout.write("\x1F"); }
  ')
  IFS=$'\x1F' read -r MCP_URL MCP_AUTH <<<"$MCP_FIELDS"
  TOOLKIT_URL="${MCPMARKET_TOOLKIT_URL:-$MCP_URL}"
  API_TOKEN="${MCPMARKET_TOKEN:-${MCP_AUTH#Bearer }}"

  if [ -z "$TOOLKIT_URL" ] || [ -z "$API_TOKEN" ]; then
    echo "MCPmarket sync: .mcp.json present but credentials unreadable — skipping sync" >&2
    exit 0
  fi
else
  TOOLKIT_URL="${MCPMARKET_TOOLKIT_URL:-}"
  API_TOKEN="${MCPMARKET_TOKEN:-}"
fi

API_BASE_URL="${MCPMARKET_API_URL:-https://app.mcpmarket.com}"

# Allowlist the API base URL by parsed host. `case` glob `*` matches
# `/`, so a pattern like `https://*.mcpmarket.com/*` also matches
# `https://attacker.com/foo.mcpmarket.com/bar` — splitting on `://`
# and `/` removes that bypass.
API_SCHEME=""
API_HOST=""
case "$API_BASE_URL" in
  https://*) API_SCHEME=https; API_HOST="${API_BASE_URL#https://}"; API_HOST="${API_HOST%%/*}" ;;
  http://*)  API_SCHEME=http;  API_HOST="${API_BASE_URL#http://}";  API_HOST="${API_HOST%%/*}" ;;
esac
# Reject userinfo (@) and multi-colon hosts — `localhost:8080@evil.com`
# would otherwise satisfy `localhost:*`.
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
if [ "$API_ALLOWED" != "true" ]; then
  echo "MCPmarket sync: api_url '$API_BASE_URL' not in allowlist — skipping sync" >&2
  exit 0
fi

if [ -z "$TOOLKIT_URL" ] || [ -z "$API_TOKEN" ] || [ -z "$PLUGIN_ROOT" ]; then
  echo "MCPmarket sync: missing configuration — skipping sync" >&2
  exit 0
fi

# Toolkit URL format: https://gateway.example.com/{orgSlug}/toolkits/{toolkitSlug}/mcp
URL_PATH=$(echo "$TOOLKIT_URL" | sed -E 's|https?://[^/]*/||; s|/mcp$||')
ORG_SLUG=$(echo "$URL_PATH" | cut -d'/' -f1)
TOOLKIT_SLUG=$(echo "$URL_PATH" | cut -d'/' -f3)

if [ -z "$ORG_SLUG" ] || [ -z "$TOOLKIT_SLUG" ]; then
  echo "MCPmarket sync: could not parse toolkit URL — skipping sync" >&2
  exit 0
fi

# Slug pattern matches server `slugSchema` (min 2 chars, no trailing dash) so
# a slug that passes here also passes the route's Zod validation.
if ! echo "$ORG_SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
  echo "MCPmarket sync: invalid org slug '$ORG_SLUG' — skipping sync" >&2
  exit 0
fi
if ! echo "$TOOLKIT_SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
  echo "MCPmarket sync: invalid toolkit slug '$TOOLKIT_SLUG' — skipping sync" >&2
  exit 0
fi

SYNC_URL="${API_BASE_URL}/api/v1/plugin/baseline?org=${ORG_SLUG}&toolkit=${TOOLKIT_SLUG}"
FAILURE_URL="${API_BASE_URL}/api/v1/plugin/sync-failure"
SYNC_APPLIED_URL="${API_BASE_URL}/api/v1/plugin/sync-applied"

# Fire-and-forget telemetry. Inlined JSON is safe only because reason
# is a hardcoded literal, slugs are regex-validated, http_code comes
# from curl's writer, and MCPMARKET_CLIENT is validated at the top of
# this script (reset to "" when the build-time substitution placeholder
# is detected, so it is always a valid enum value or empty string).
report_failure() {
  local reason="$1"
  local http_code="${2:-}"
  local http_code_frag=""
  local client_frag=""
  [ -n "$http_code" ] && http_code_frag=$(printf ',"httpCode":%s' "$http_code")
  [ -n "$MCPMARKET_CLIENT" ] && client_frag=$(printf ',"client":"%s"' "$MCPMARKET_CLIENT")
  local payload
  payload=$(printf '{"reason":"%s","orgSlug":"%s","toolkitSlug":"%s"%s%s}' \
    "$reason" "$ORG_SLUG" "$TOOLKIT_SLUG" "$http_code_frag" "$client_frag")
  curl -sS --max-time 3 -X POST \
    -H "Authorization: Bearer $API_TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: $USER_AGENT" \
    ${CLIENT_HEADER_ARGS[@]+"${CLIENT_HEADER_ARGS[@]}"} \
    -d "$payload" \
    "$FAILURE_URL" >/dev/null 2>&1 || true
}

SKILLS_DIR="${MCPMARKET_SKILLS_DIR:-$PLUGIN_ROOT/skills}"
mkdir -p "$SKILLS_DIR"

TMPFILE=$(mktemp)
CURL_ERR=$(mktemp)
trap 'rm -f "$TMPFILE" "$CURL_ERR"' EXIT

# `${arr[@]+"${arr[@]}"}` is the empty-array-safe expansion under
# `set -u` (bash 4.2 on macOS errors on bare `${arr[@]}` for empties).
CLIENT_HEADER_ARGS=()
if [ -n "$MCPMARKET_CLIENT" ]; then
  CLIENT_HEADER_ARGS=(-H "X-MCPmarket-Client: $MCPMARKET_CLIENT")
fi

HTTP_CODE=$(curl -sS -o "$TMPFILE" -w '%{http_code}' --max-time 15 \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Accept: application/json" \
  -H "User-Agent: $USER_AGENT" \
  ${CLIENT_HEADER_ARGS[@]+"${CLIENT_HEADER_ARGS[@]}"} \
  "$SYNC_URL" 2>"$CURL_ERR") || {
  ERR=$(tr -d '\n' < "$CURL_ERR" | head -c 200)
  echo "MCPmarket sync: network error — using cached skills (${ERR:-no detail})" >&2
  report_failure "network_error"
  exit 0
}

if [ "$HTTP_CODE" != "200" ]; then
  echo "MCPmarket sync: API returned HTTP $HTTP_CODE — using cached skills" >&2
  report_failure "http_error" "$HTTP_CODE"
  exit 0
fi

# Single node pass emits records the bash loop below consumes. Inter-field
# delimiter is \x1F (Unit Separator) — tab is IFS-whitespace and bash
# `read` collapses runs of tabs, so an empty middle field (e.g. server
# emits a skill with no version) would shift later fields left and
# corrupt downstream parsing. \x1F is non-whitespace and preserves
# empty fields. Same rationale as the .mcp.json credential parser above.
#   S<US><slug><US><version><US><base64-content><US><sha>
#   F<US><slug><US><path><US><base64-content>
#   D<US><slug-or-empty><US><index><US><reason>
# Rejects \t/\n/\x00/\x1F in string fields so a server can't forge extra
# records by smuggling \n into a slug or path or smuggling \x1F to
# create extra positional fields. When a field IS unsafe, a `D` record
# is emitted instead so the drop surfaces in /sync-applied telemetry
# rather than silently disappearing. Reason vocabulary matches
# `pluginSkillDropReasonSchema` in packages/validators/src/plugin.ts —
# extend both together.
US=$'\x1F'
RECORDS=$(node -e '
  const UNSAFE = /[\t\n\x00\x1F]/;
  // Slug format check — must match the server slugSchema regex in
  // packages/validators/src/common.ts (SLUG_PATTERN) plus its
  // .min(2)/.max(50) length bounds. The parser pre-validates the slug
  // BEFORE emitting any drop record that would carry it, because
  // pluginSkillDropSchema only allows null slug for `corrupted_slug`
  // and `invalid_slug` — emitting a format-bad slug under any other
  // reason (e.g. unsafe_version, invalid_file_path) would 400 the
  // entire /sync-applied payload via slugSchema rejection and lose all
  // sibling drop telemetry for the run.
  const SLUG_RE = /^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/;
  const slugOk = (s) =>
    typeof s === "string" && s.length >= 2 && s.length <= 50 && SLUG_RE.test(s);
  const crypto = require("crypto");
  let raw;
  try { raw = require("fs").readFileSync(0, "utf8"); } catch { process.exit(1); }
  let r;
  try { r = JSON.parse(raw); } catch { process.exit(1); }
  const skills = r && r.data && r.data.skills;
  if (!Array.isArray(skills)) process.exit(1);
  const out = [];
  skills.forEach((s, idx) => {
    if (!s || typeof s.slug !== "string") {
      // Missing or non-string slug — same severity as a control-char
      // slug. Echo back as corrupted_slug with the array index so the
      // server can locate the row without trusting client-sent strings.
      out.push(["D", "", String(idx), "corrupted_slug"].join("\x1F"));
      return;
    }
    if (UNSAFE.test(s.slug)) {
      out.push(["D", "", String(idx), "corrupted_slug"].join("\x1F"));
      return;
    }
    if (!slugOk(s.slug)) {
      // Slug passed UNSAFE but fails slugSchema (uppercase, too short,
      // too long, leading/trailing dash). Emit invalid_slug with null
      // slug + index. The malformed slug must not flow into downstream
      // D records (unsafe_version, invalid_file_path) — those reasons
      // require a slugSchema-compliant slug or the whole payload 400s.
      // Same shape rationale as corrupted_slug: index is the only
      // server-trustable locator.
      out.push(["D", "", String(idx), "invalid_slug"].join("\x1F"));
      return;
    }
    const version = s.version || "";
    if (UNSAFE.test(version)) {
      // Version is unsafe but the slug is format-valid (pre-check above
      // gated this branch). Preserve the slug so the server can
      // identify which skill rejected.
      out.push(["D", s.slug, String(idx), "unsafe_version"].join("\x1F"));
      return;
    }
    const content = s.content || "";
    const c = Buffer.from(content, "utf8").toString("base64");
    // Hash the *normalized* bytes the bash write path produces, not the
    // raw server content. Bash command substitution `$(...)` strips all
    // trailing newlines, and `printf '%s\n'` then appends exactly one —
    // so what lands on disk is always `content with trailing \n+
    // collapsed to one`. Hashing the raw server bytes here would flag
    // every freshly-written file as diverged whenever the server sent
    // content without exactly one trailing newline.
    const normalized = content.replace(/\n+$/, "") + "\n";
    const sha = crypto.createHash("sha256").update(normalized, "utf8").digest("hex");
    out.push(["S", s.slug, version, c, sha].join("\x1F"));
    if (Array.isArray(s.files)) {
      for (const f of s.files) {
        if (!f || typeof f.path !== "string" || UNSAFE.test(f.path)) {
          // File path is unsafe — emit a drop record keyed by the
          // parent skill (no index for files since the F-record path
          // is the natural identifier and is the corrupted thing here).
          out.push(["D", s.slug, "", "invalid_file_path"].join("\x1F"));
          continue;
        }
        const fc = Buffer.from(f.content || "", "utf8").toString("base64");
        out.push(["F", s.slug, f.path, fc].join("\x1F"));
      }
    }
  });
  process.stdout.write(out.join("\n"));
' < "$TMPFILE") || {
  echo "MCPmarket sync: invalid response — using cached skills" >&2
  report_failure "invalid_response"
  exit 0
}

# `printf '%s\n'` restores the trailing newline that `$()` strips.
SKILLS_TSV=$(printf '%s\n' "$RECORDS" | awk -F"$US" -v US="$US" '$1=="S" { print $2 US $3 US $4 US $5 }')
# Drops emitted by the node parser (corrupted_slug, unsafe_version,
# invalid_file_path). Field shape: <slug-or-empty><US><index-or-empty><US><reason>
# Bash-side drops (invalid_slug, empty_content, hand_placed_collision)
# get appended to DROPS_TSV later inside the write loop.
DROPS_TSV=$(printf '%s\n' "$RECORDS" | awk -F"$US" -v US="$US" '$1=="D" { print $2 US $3 US $4 }')
SKILL_COUNT=$(printf '%s\n' "$SKILLS_TSV" | awk 'NF { c++ } END { print c+0 }')

if [ "$SKILL_COUNT" -eq 0 ]; then
  echo "MCPmarket sync: no baseline skills configured"
  # Fall through to the cleanup pass + sync_applied POST. If the user
  # had skills synced before the toolkit's baseline was emptied, the
  # cleanup loop still needs to delete them; and the server still
  # wants a delta event so "this user has the plugin but their
  # toolkit is empty" shows up in analytics instead of looking like
  # silence.
fi

# Slugs the plugin ships itself (never synced, never pruned). The
# plugin no longer bundles any skill — the `/sync` skill was removed
# because the Bash-tool sandbox makes the plugin's `skills/` dir
# read-only to agent-invoked writes, so it could never re-sync. The
# guard stays (empty) so a future bundled skill can be re-protected
# without re-plumbing the write/cleanup loops, and so an existing
# install's stale `skills/sync/` is pruned on the next sync.
BUNDLED_SKILLS=""

SYNCED_SLUGS=()

# Sync delta counters. The server-side `plugin.sync` heartbeat only
# knows what it sent — it can't see what was on disk before. These
# counters capture that delta so the sync_applied POST below can
# answer "does sync change anything for this user?".
SKILLS_NEW=0
SKILLS_UPDATED=0
SKILLS_UNCHANGED=0
SKILLS_REMOVED=0
# `skills_diverged` is a *subset* of `skills_unchanged` — version stamp
# matches the server's, but the on-disk content has been edited locally.
# Recorded for divergence-rate analytics; sync intentionally does NOT
# overwrite (users may legitimately customize installed skills).
SKILLS_DIVERGED=0
# Bundled-skill drops collected during the write loop. Appended to
# DROPS_TSV before payload assembly. Newline-delimited records of
# `<slug><US><><US><reason>` (empty index field — bash-side drops
# don't have a baseline-array index).
BASH_DROPS=""
append_drop() {
  # $1: slug (empty string for corrupted_slug, never used by this helper)
  # $2: reason
  if [ -z "$BASH_DROPS" ]; then
    BASH_DROPS="${1}${US}${US}${2}"
  else
    BASH_DROPS=$(printf '%s\n%s' "$BASH_DROPS" "${1}${US}${US}${2}")
  fi
}

while IFS=$'\x1F' read -r SLUG VERSION CONTENT_B64 EXPECTED_SHA; do
  if [ -z "$SLUG" ] || [ "$SLUG" = "null" ]; then
    continue
  fi

  # Required: a server-returned `..` would otherwise pivot SKILL_DIR to
  # $PLUGIN_ROOT and overwrite the agent's startup hook. Slug has
  # already passed the node UNSAFE filter (no \t/\n/\x00/\x1F) — failing
  # here means the slug format is wrong (e.g. uppercase, leading dash,
  # slash, single char). The slug itself can't be echoed back to the
  # server: pluginSkillDropSchema enforces slug → slugSchema regex, so
  # sending the malformed slug would 400 the entire payload and lose
  # all sibling drops. Send as null instead (same shape as
  # corrupted_slug from the node parser); the server still gets the
  # count + reason, just not the slug detail. Defense-in-depth path —
  # shouldn't fire in production since server only emits validated
  # slugs.
  if ! echo "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
    echo "MCPmarket sync: skipping skill with invalid slug '$SLUG'" >&2
    append_drop "" "invalid_slug"
    continue
  fi

  case " $BUNDLED_SKILLS " in
    *" $SLUG "*) continue ;;
  esac

  SYNCED_SLUGS+=("$SLUG")
  SKILL_DIR="$SKILLS_DIR/$SLUG"
  mkdir -p "$SKILL_DIR"

  LOCAL_VERSION=""
  if [ -f "$SKILL_DIR/SKILL.md" ]; then
    LOCAL_VERSION=$(awk '
      /^---[[:space:]]*$/ { if (in_fm) { exit } else { in_fm=1; next } }
      in_fm && /^[[:space:]]+mcpmarket-version:/ {
        sub(/^[[:space:]]+mcpmarket-version:[[:space:]]*/, "")
        gsub(/^["'\'']|["'\'']$/, "")
        print; exit
      }
    ' "$SKILL_DIR/SKILL.md")
  fi
  if [ -n "$LOCAL_VERSION" ] && [ "$LOCAL_VERSION" = "$VERSION" ]; then
    # Version stamp matches. Check whether content has drifted from
    # what we sent — record-only, no overwrite, no user-visible warning.
    # Hashing prefers `shasum -a 256` (BSD/macOS default) over
    # `sha256sum` (GNU/Linux) so the check works on both without an
    # extra dependency; node fallback covers exotic environments.
    LOCAL_SHA=""
    if command -v shasum >/dev/null 2>&1; then
      LOCAL_SHA=$(shasum -a 256 "$SKILL_DIR/SKILL.md" 2>/dev/null | awk '{print $1}')
    elif command -v sha256sum >/dev/null 2>&1; then
      LOCAL_SHA=$(sha256sum "$SKILL_DIR/SKILL.md" 2>/dev/null | awk '{print $1}')
    else
      LOCAL_SHA=$(SKILL_PATH="$SKILL_DIR/SKILL.md" node -e '
        try {
          const fs = require("fs");
          const crypto = require("crypto");
          const data = fs.readFileSync(process.env.SKILL_PATH);
          process.stdout.write(crypto.createHash("sha256").update(data).digest("hex"));
        } catch { process.exit(0); }
      ' 2>/dev/null)
    fi
    if [ -n "$LOCAL_SHA" ] && [ -n "$EXPECTED_SHA" ] && [ "$LOCAL_SHA" != "$EXPECTED_SHA" ]; then
      SKILLS_DIVERGED=$((SKILLS_DIVERGED + 1))
    fi
    SKILLS_UNCHANGED=$((SKILLS_UNCHANGED + 1))
    continue
  fi

  # The plugin's `skills/` directory is sync-owned. Users don't hand-place
  # files there — skill creation goes through the web UI and propagates
  # via the baseline endpoint. Earlier versions (≤0.6.0) gated overwrites
  # on a `mcpmarket-version:` stamp to "protect hand-placed files," but
  # that scenario isn't a real workflow, and the gate's side effect was
  # silently dropping every sync for unstamped baseline content — see the
  # May 2026 plugin-sync investigation. The sync contract is "sync owns
  # this directory"; honor it and overwrite. A user who edits a synced
  # SKILL.md in place would always have been overwritten on the next
  # session anyway (it's how the tool works); removing the gate just
  # makes that contract honest.

  # Only count + write when the server actually sent content. An empty
  # CONTENT_B64 with no local SKILL.md would otherwise increment
  # SKILLS_NEW on every sync without ever writing, making the delta
  # event lie about on-disk state.
  if [ -z "$CONTENT_B64" ]; then
    append_drop "$SLUG" "empty_content"
    continue
  fi

  if [ -z "$LOCAL_VERSION" ]; then
    SKILLS_NEW=$((SKILLS_NEW + 1))
  else
    SKILLS_UPDATED=$((SKILLS_UPDATED + 1))
  fi

  TMP_SKILL="$(mktemp "$SKILL_DIR/SKILL.md.XXXX")"
  printf '%s\n' "$(echo "$CONTENT_B64" | base64 -d)" > "$TMP_SKILL"
  mv "$TMP_SKILL" "$SKILL_DIR/SKILL.md"

  FILES_TSV=$(printf '%s\n' "$RECORDS" | awk -F"$US" -v US="$US" -v slug="$SLUG" '$1=="F" && $2==slug { print $3 US $4 }')
  if [ -n "$FILES_TSV" ]; then
    while IFS=$'\x1F' read -r FILE_PATH FILE_CONTENT_B64; do
      if [ -z "$FILE_PATH" ] || [ "$FILE_PATH" = "null" ]; then
        continue
      fi

      # Segment-level path-traversal guard; `case` globs miss bare `..`
      # and `foo/..`.
      _PATH_OK=true
      case "$FILE_PATH" in /*) _PATH_OK=false ;; esac
      if [ "$_PATH_OK" = "true" ]; then
        IFS='/' read -ra _SEGS <<<"$FILE_PATH"
        for _SEG in "${_SEGS[@]}"; do
          if [ "$_SEG" = ".." ] || [ "$_SEG" = "." ] || [ -z "$_SEG" ]; then
            _PATH_OK=false
            break
          fi
        done
      fi
      if [ "$_PATH_OK" != "true" ]; then
        # Bash-side path traversal rejection — distinct from the node
        # parser's `invalid_file_path` (which fires on control-char
        # paths). Same reason enum since both represent "the file's
        # path is unsafe to write to disk"; the server can't usefully
        # distinguish the two sub-causes and we'd rather keep the
        # closed enum tight.
        append_drop "$SLUG" "invalid_file_path"
        continue
      fi

      FILE_DIR=$(dirname "$SKILL_DIR/$FILE_PATH")
      mkdir -p "$FILE_DIR"
      TMP_FILE="$(mktemp "$SKILL_DIR/$FILE_PATH.XXXX")"
      printf '%s\n' "$(echo "$FILE_CONTENT_B64" | base64 -d)" > "$TMP_FILE"
      mv "$TMP_FILE" "$SKILL_DIR/$FILE_PATH"
    done <<EOF
$FILES_TSV
EOF
  fi

  rm -f "$SKILL_DIR/.version"
done <<EOF
$SKILLS_TSV
EOF

# Cleanup: delete subdirs whose slug isn't in the current baseline.
# Symmetric with the write path above — the plugin's skills/ directory
# is sync-owned, so an entry without a matching baseline slug is stale
# and should be pruned. Bundled skills (e.g. `sync`) are excluded
# because they're shipped by the plugin itself, not by the catalog.
if [ -d "$SKILLS_DIR" ] && [ "$SKILLS_DIR" != "/" ]; then
  for EXISTING in "$SKILLS_DIR"/*/; do
    [ -d "$EXISTING" ] || continue
    EXISTING_SLUG=$(basename "$EXISTING")
    case " $BUNDLED_SKILLS " in
      *" $EXISTING_SLUG "*) continue ;;
    esac

    [ -f "$EXISTING/SKILL.md" ] || continue

    FOUND=false
    # Length check required: `"${empty[@]:-}"` expands to one empty word.
    if [ ${#SYNCED_SLUGS[@]} -gt 0 ]; then
      for S in "${SYNCED_SLUGS[@]}"; do
        if [ "$S" = "$EXISTING_SLUG" ]; then
          FOUND=true
          break
        fi
      done
    fi
    if [ "$FOUND" = "false" ]; then
      rm -rf "$EXISTING"
      SKILLS_REMOVED=$((SKILLS_REMOVED + 1))
    fi
  done
fi

# Combine node-emitted and bash-emitted drop records. Both share the
# same `<slug-or-empty><US><index-or-empty><US><reason>` shape so they
# stream through the same downstream JSON builder.
ALL_DROPS=""
if [ -n "$DROPS_TSV" ] && [ -n "$BASH_DROPS" ]; then
  ALL_DROPS=$(printf '%s\n%s' "$DROPS_TSV" "$BASH_DROPS")
elif [ -n "$DROPS_TSV" ]; then
  ALL_DROPS="$DROPS_TSV"
elif [ -n "$BASH_DROPS" ]; then
  ALL_DROPS="$BASH_DROPS"
fi

# Sync delta POST. Counters remain pure non-negative integers; the
# `skillsDropped` array carries per-skill detail when the server's
# baseline contained skills the client couldn't write. Slugs in
# `skillsDropped` came from the server in the same response cycle —
# echoing them back reveals nothing new (see pluginSkillDropSchema
# docstring in packages/validators/src/plugin.ts).
#
# `client` is in the body (not just the header) because the
# /sync-applied route reads it from the validated body — without this
# every event from a client-specific install loses metadata.client.
#
# Built via node rather than printf so the `skillsDropped` JSON shape
# stays robust against any drop reason vocabulary extension and so
# string escaping (slugs in the array) doesn't have to be re-derived
# in bash. Counters are passed as env vars; ALL_DROPS streams on stdin
# so embedded newlines (the inter-record separator) survive intact.
SYNC_APPLIED_PAYLOAD=$(
  ORG="$ORG_SLUG" TK="$TOOLKIT_SLUG" \
  S_NEW="$SKILLS_NEW" S_UPD="$SKILLS_UPDATED" S_UNCH="$SKILLS_UNCHANGED" \
  S_REM="$SKILLS_REMOVED" S_DIV="$SKILLS_DIVERGED" \
  CLIENT="$MCPMARKET_CLIENT" \
  node -e '
    // Closed-enum allowlist. MUST be kept in lockstep with
    // `pluginSkillDropReasonSchema` in
    // packages/validators/src/plugin.ts — unrecognized reasons are
    // silently dropped here (the `continue` below), so a new reason
    // added to the Zod enum without updating this Set never reaches
    // the server. See the four-site contract in that schema docstring.
    // `hand_placed_collision` is intentionally absent — 0.7.0 removed
    // the emit site (plugin skills/ is sync-owned; unstamped local
    // files are our past output, not hand-placements). The reason
    // stays in the server Zod enum for backwards compat with older
    // clients, but a new sync.sh that produced one would be a bug.
    const REASONS = new Set([
      "invalid_slug",
      "unsafe_version",
      "empty_content",
      "invalid_file_path",
      "corrupted_slug",
    ]);
    // Reasons where the schema accepts null slug. Must match the
    // NULL_SLUG_REASONS set in pluginSkillDropSchema.superRefine
    // (packages/validators/src/plugin.ts) — drift here causes the
    // payload to either drop legitimate records (if too restrictive)
    // or 400 the whole POST (if too permissive).
    const NULL_SLUG_REASONS = new Set(["corrupted_slug", "invalid_slug"]);
    const dropsRaw = require("fs").readFileSync(0, "utf8");
    const drops = [];
    for (const line of dropsRaw.split("\n")) {
      if (!line) continue;
      const parts = line.split("\x1F");
      if (parts.length !== 3) continue;
      const [slug, indexStr, reason] = parts;
      if (!REASONS.has(reason)) continue;
      const entry = { reason };
      const n = Number.parseInt(indexStr, 10);
      const hasIndex = Number.isFinite(n) && n >= 0;
      if (reason === "corrupted_slug") {
        // corrupted_slug MUST have null slug (node UNSAFE filter
        // rejected the bytes) AND carries the baseline-array index
        // so the server can locate the bad row.
        entry.slug = null;
        if (hasIndex) entry.index = n;
      } else if (NULL_SLUG_REASONS.has(reason) && !slug) {
        // Other null-slug-allowed reasons (invalid_slug). The node
        // parser emits these WITH an index (baseline-array position);
        // the bash defense-in-depth branch emits them WITHOUT an
        // index (write loop has no access to the original position).
        // Forward the index when present so node-emitted drops carry
        // the locator and bash-emitted drops still validate.
        entry.slug = null;
        if (hasIndex) entry.index = n;
      } else {
        // Schema disallows null slug for the remaining reasons. Drop
        // the record rather than send something the server will 400.
        if (!slug) continue;
        entry.slug = slug;
        if (hasIndex) entry.index = n;
      }
      drops.push(entry);
    }
    const body = {
      orgSlug: process.env.ORG,
      toolkitSlug: process.env.TK,
      skillsNew: Number(process.env.S_NEW),
      skillsUpdated: Number(process.env.S_UPD),
      skillsUnchanged: Number(process.env.S_UNCH),
      skillsRemoved: Number(process.env.S_REM),
      skillsDiverged: Number(process.env.S_DIV),
    };
    if (drops.length > 0) {
      body.skillsDropped = drops.slice(0, 100);
      // Signal the server when the array was truncated so analytics
      // queries on skillsDropped.length can treat the count as a
      // lower bound rather than the exact drop count. Match the
      // omit-when-empty contract: only emit the field on overflow.
      if (drops.length > 100) body.skillsDroppedTruncated = true;
    }
    if (process.env.CLIENT) body.client = process.env.CLIENT;
    process.stdout.write(JSON.stringify(body));
  ' <<EOF
$ALL_DROPS
EOF
) || SYNC_APPLIED_PAYLOAD=""

if [ -n "$SYNC_APPLIED_PAYLOAD" ]; then
  curl -sS --max-time 3 -X POST \
    -H "Authorization: Bearer $API_TOKEN" \
    -H "Content-Type: application/json" \
    -H "User-Agent: $USER_AGENT" \
    ${CLIENT_HEADER_ARGS[@]+"${CLIENT_HEADER_ARGS[@]}"} \
    -d "$SYNC_APPLIED_PAYLOAD" \
    "$SYNC_APPLIED_URL" >/dev/null 2>&1 || true
fi

# Touch the TTL sentinel only AFTER the full success path completes
# (HTTP 200 + parse OK + write loop + sync_applied payload built +
# POST attempted). Any earlier exit (network error, HTTP non-200,
# invalid baseline response, sync_applied payload-build failure) skips
# the touch so the next session retries immediately rather than caching
# the failure. The POST itself is fire-and-forget — a 4xx/5xx on
# /sync-applied doesn't invalidate the sync from the client's
# perspective (skills were still written) so we touch even when the
# POST errors silently. But if the local payload builder itself failed
# (e.g., node crashed mid-build, $SYNC_APPLIED_PAYLOAD is empty), we
# never even tried to deliver telemetry — treat that as a parse-class
# failure and skip the touch.
if [ -n "$SYNC_APPLIED_PAYLOAD" ]; then
  touch "$SYNC_SENTINEL" 2>/dev/null || true
fi

# Print the count of skills actually written to disk this run, not the
# count of skills in the API response. Pre-0.6.0 this printed
# $SKILL_COUNT (response count), which lied whenever any per-skill
# drop kicked in — a user with 4 skills sent and 3 dropped would see
# "MCPmarket sync: 4 baseline skill(s) synced" while only 1 SKILL.md
# landed on disk. SKILLS_NEW + SKILLS_UPDATED reflects the actual
# write count.
SKILLS_WRITTEN=$((SKILLS_NEW + SKILLS_UPDATED))
echo "MCPmarket sync: $SKILLS_WRITTEN baseline skill(s) synced"
