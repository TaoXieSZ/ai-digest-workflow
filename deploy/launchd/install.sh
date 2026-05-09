#!/bin/bash
# Install launchd jobs for ai-digest-workflow:
#   - fetch    every 30 minutes  → scripts/run_fetch.py
#   - radar    every hour        → scripts/run_radar.py
#   - digest   daily 12:00 local → scripts/run_digest.py
#   - calendar every hour        → scripts/run_calendar_sync.py --confirm
#
# Re-running this script is safe: it boots out any existing job before
# bootstrapping the fresh plist.

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

mkdir -p "$LA_DIR"
mkdir -p "$ROOT/data/logs"
chmod +x "$ROOT/deploy/launchd/run.sh"
chmod +x "$ROOT/deploy/launchd/wiki_ingest.sh"

# write_plist <name> <schedule_xml> <script_filename> [extra_arg...]
write_plist() {
  local name=$1
  local schedule=$2
  local script=$3
  shift 3
  local label="com.txie.ai-digest.${name}"
  local plist="${LA_DIR}/${label}.plist"

  # Build ProgramArguments xml
  local args_xml=""
  args_xml+="      <string>${ROOT}/deploy/launchd/run.sh</string>\n"
  args_xml+="      <string>scripts/${script}</string>\n"
  for extra in "$@"; do
    args_xml+="      <string>${extra}</string>\n"
  done

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
$(printf "${args_xml}")    </array>
    <key>WorkingDirectory</key>
    <string>${ROOT}</string>
    ${schedule}
    <key>StandardOutPath</key>
    <string>${ROOT}/data/logs/${name}.out.log</string>
    <key>StandardErrorPath</key>
    <string>${ROOT}/data/logs/${name}.err.log</string>
    <key>RunAtLoad</key>
    <false/>
    <!-- ProcessType=Background lets macOS deprioritize so we don't fight
         interactive apps; throttling still applies. -->
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
EOF

  # Idempotent: bootout if already loaded, then bootstrap.
  launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
  launchctl bootstrap "${DOMAIN}" "${plist}"
  echo "✓ installed ${label}"
}

# fetch: every 30 minutes (1800s)
write_plist fetch \
  "<key>StartInterval</key><integer>1800</integer>" \
  run_fetch.py

# radar: every hour (3600s). Quiet hours (23-7 CST) handled inside event_radar
# so push is suppressed but classification still runs.
write_plist radar \
  "<key>StartInterval</key><integer>3600</integer>" \
  run_radar.py

# digest: daily at 12:00 local time. Single fixed time is enough — the digest
# is "yesterday's roundup", not real-time.
write_plist digest \
  "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>" \
  run_digest.py

# calendar: every hour. Pushes newly-classified event-items into the configured
# Feishu calendar. Idempotent (DB ledger gates duplicates), so even if it races
# with radar / fetch, no duplicate events are created.
write_plist calendar \
  "<key>StartInterval</key><integer>3600</integer>" \
  run_calendar_sync.py --confirm

# wiki_ingest: daily 12:30 — 30 min after digest writes daily-{date}.md so the
# file is reliably present. Spawns a one-shot `claude -p` session that calls
# the OMC wiki_ingest MCP tool. Plist points DIRECTLY to wiki_ingest.sh
# (bypasses run.sh because the runner is shell-based, not Python).
WIKI_LABEL="com.txie.ai-digest.wiki_ingest"
WIKI_PLIST="${LA_DIR}/${WIKI_LABEL}.plist"
cat > "$WIKI_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${WIKI_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
      <string>${ROOT}/deploy/launchd/wiki_ingest.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${ROOT}</string>
    <key>StartCalendarInterval</key>
    <dict>
      <key>Hour</key><integer>12</integer>
      <key>Minute</key><integer>30</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${ROOT}/data/logs/wiki_ingest.out.log</string>
    <key>StandardErrorPath</key>
    <string>${ROOT}/data/logs/wiki_ingest.err.log</string>
    <key>RunAtLoad</key>
    <false/>
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
EOF
launchctl bootout "${DOMAIN}/${WIKI_LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "${WIKI_PLIST}"
echo "✓ installed ${WIKI_LABEL}"

echo
echo "All 5 jobs installed in ${DOMAIN}."
echo "Inspect:   launchctl list | grep ai-digest"
echo "Logs:      tail -F ${ROOT}/data/logs/{fetch,radar,digest,calendar,wiki_ingest}.{out,err}.log"
echo "Uninstall: ${ROOT}/deploy/launchd/uninstall.sh"
