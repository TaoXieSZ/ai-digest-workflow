#!/bin/bash
# Entry script for launchd: cd into project root, load .env files,
# then exec .venv/bin/python with the script + args passed in.
#
# Usage (called by launchd plist):
#   /path/to/deploy/launchd/run.sh scripts/run_fetch.py
#   /path/to/deploy/launchd/run.sh scripts/run_digest.py --dry-run

set -e

# Project root = two levels up from this script.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Load env files. We export everything that's set; missing files are skipped
# silently (so repos without wewe-rss still work).
set -a
[ -f .env ] && . .env
[ -f deploy/wewe-rss/.env ] && . deploy/wewe-rss/.env
set +a

mkdir -p data/logs

# Stamp each invocation so cron logs are easy to scan.
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting: $*" >&2

exec .venv/bin/python "$@"
