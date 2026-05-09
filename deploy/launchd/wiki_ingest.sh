#!/bin/bash
# Daily wiki ingest runner — spawned by launchd ~30min after run_digest finishes.
#
# Reads data/digests/daily-{today}.md (the digest builder's local archive)
# and asks a one-shot Claude session to call the OMC `wiki_ingest` MCP tool.
# That tool writes a page into .omc/wiki/<slug>.md and updates index.md.
#
# Idempotency: wiki_ingest itself merges into an existing same-slug page, so
# running this twice on the same date is safe.
#
# Skips silently when:
#   - today's digest md doesn't exist yet (run_digest hasn't run)
#   - claude CLI isn't found
#
# Cost note: one daily call is ~500-1500 tokens, ~$0.005-$0.02 with Sonnet.

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

mkdir -p data/logs

# Augment PATH so launchd's minimal env can still find `claude` and `node`.
# launchd starts with /usr/bin:/bin:/usr/sbin:/sbin only.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"

TODAY="${1:-$(date +%Y-%m-%d)}"
DIGEST="$ROOT/data/digests/daily-${TODAY}.md"

stamp() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >&2
}

if [ ! -f "$DIGEST" ]; then
  stamp "no digest at $DIGEST; skip wiki ingest"
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  stamp "ERROR: claude CLI not in PATH ($PATH)"
  exit 1
fi

stamp "ingesting $DIGEST to wiki"

# Pass the file via -p prompt. The session has access to all OMC MCP tools
# including wiki_ingest. Use --output-format text and --max-turns to keep it
# bounded — one tool call + one short reply is all we need.
PROMPT="Read the file at $DIGEST and call the wiki_ingest MCP tool with these arguments:
  - title: 'AI Digest ${TODAY}'
  - content: the full markdown content of that file
  - tags: ['daily-digest', 'ai-news', '${TODAY}']
  - category: 'reference'
After the tool returns, reply with one short line confirming the slug it created. Do not ingest anything else."

# --allowedTools: scope this non-interactive session to exactly the two tools
# we need — Read (to load the digest md) and the OMC wiki_ingest MCP tool.
# wiki_ingest is also pre-approved in the user's ~/.claude/settings.json
# permissions.allow, so no prompt blocks the cron.
exec claude -p "$PROMPT" \
  --output-format text \
  --allowedTools 'Read,mcp__plugin_oh-my-claudecode_t__wiki_ingest'

