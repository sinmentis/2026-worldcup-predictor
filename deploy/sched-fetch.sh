#!/usr/bin/env bash
# Self-terminating fetch loop for tonight. Usage: sched-fetch.sh <news|odds> <interval_seconds> <end_epoch>
set -u
kind="$1"
interval="$2"
end_epoch="$3"
repo="$(cd "$(dirname "$0")/.." && pwd)"
log="/tmp/wc-sched-${kind}.log"
echo $$ > "/tmp/wc-sched-${kind}.pid"
echo "$(date '+%F %T') ${kind} schedule started (every ${interval}s until $(date -d @${end_epoch} '+%F %T'))" >> "$log"
cd "$repo" || exit 1
export WC_DB_PATH="$repo/data/worldcup.db"
while [ "$(date +%s)" -lt "$end_epoch" ]; do
  "$repo/.venv/bin/worldcup" "fetch-${kind}" >> "$log" 2>&1
  # After refreshing odds, log any newly-flagged value bets into the paper-trading ledger.
  if [ "$kind" = "odds" ]; then
    "$repo/.venv/bin/worldcup" paper-log >> "$log" 2>&1
  fi
  echo "$(date '+%F %T') ${kind} tick done" >> "$log"
  sleep "$interval"
done
echo "$(date '+%F %T') ${kind} schedule ended" >> "$log"
rm -f "/tmp/wc-sched-${kind}.pid"
