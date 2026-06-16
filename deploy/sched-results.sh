#!/usr/bin/env bash
# Results fetch loop: pull finished results every interval and re-run the simulation when a new
# result lands, until <end_epoch>. Usage: sched-results.sh <interval_seconds> <end_epoch>
set -u
interval="$1"
end_epoch="$2"
repo="/home/shunlyu/work/worldcup-predictor"
log="/tmp/wc-sched-results.log"
db="$repo/data/worldcup.db"
echo $$ > /tmp/wc-sched-results.pid
echo "$(date '+%F %T') results schedule started (every ${interval}s until $(date -d @${end_epoch} '+%F %T'))" >> "$log"
cd "$repo" || exit 1
export WC_DB_PATH="$db"
finished_count() {
  "$repo/.venv/bin/python" -c "import sqlite3;print(sqlite3.connect('$db').execute(\"SELECT COUNT(*) FROM matches WHERE status='FINISHED'\").fetchone()[0])"
}
while [ "$(date +%s)" -lt "$end_epoch" ]; do
  before=$(finished_count)
  "$repo/.venv/bin/worldcup" fetch-fixtures >> "$log" 2>&1
  after=$(finished_count)
  if [ "$after" -gt "$before" ]; then
    echo "$(date '+%F %T') new result(s): $before -> $after, re-simulating" >> "$log"
    "$repo/.venv/bin/worldcup" simulate --n 20000 >> "$log" 2>&1
  fi
  echo "$(date '+%F %T') results tick done (finished=$after)" >> "$log"
  sleep "$interval"
done
echo "$(date '+%F %T') results schedule ended" >> "$log"
rm -f /tmp/wc-sched-results.pid
