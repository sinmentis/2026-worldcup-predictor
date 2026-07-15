#!/usr/bin/env bash
# Results fetch loop: pull results every interval and refresh model state when match content
# changes, until <end_epoch>. Usage: sched-results.sh <interval_seconds> <end_epoch>
set -uo pipefail
interval="$1"
end_epoch="$2"
repo="$(cd "$(dirname "$0")/.." && pwd)"
log="${WC_SCHED_RESULTS_LOG:-/tmp/wc-sched-results.log}"
lock="${WC_SCHED_RESULTS_LOCK:-/tmp/wc-sched-results.lock}"
pid_file="${WC_SCHED_RESULTS_PID:-/tmp/wc-sched-results.pid}"
db="$repo/data/worldcup.db"
worldcup="${WC_WORLDCUP_BIN:-$repo/.venv/bin/worldcup}"

exec 9>"$lock"
if ! flock -n 9; then
  echo "$(date '+%F %T') results schedule already running" >> "$log"
  exit 0
fi

echo $$ > "$pid_file"
trap 'rm -f "$pid_file"' EXIT
echo "$(date '+%F %T') results schedule started (every ${interval}s until $(date -d @${end_epoch} '+%F %T'))" >> "$log"
cd "$repo" || exit 1
export WC_DB_PATH="$db"

while [ "$(date +%s)" -lt "$end_epoch" ]; do
  tick_failed=0
  if ! "$worldcup" fetch-fixtures >> "$log" 2>&1; then
    echo "$(date '+%F %T') results tick failed: fetch-fixtures" >> "$log"
    tick_failed=1
  fi
  if ! "$worldcup" paper-settle >> "$log" 2>&1; then
    echo "$(date '+%F %T') results tick failed: paper-settle" >> "$log"
    tick_failed=1
  fi
  if ! "$worldcup" refresh-results-model --n 20000 >> "$log" 2>&1; then
    echo "$(date '+%F %T') results tick failed: refresh-results-model (will retry)" >> "$log"
    tick_failed=1
  fi
  if [ "$tick_failed" -eq 0 ]; then
    echo "$(date '+%F %T') results tick done" >> "$log"
  else
    echo "$(date '+%F %T') results tick done with failures" >> "$log"
  fi
  sleep "$interval"
done
echo "$(date '+%F %T') results schedule ended" >> "$log"
