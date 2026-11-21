#!/usr/bin/env bash
# Keeps the AIS recorder alive across wifi switches.
#
# Why: after a network change the recorder's websocket reconnect can hang without exiting
# (seen twice on Sat 21 Nov: 14:15 and 14:41 ET), so "process alive" is not "recording".
# This loop restarts it whenever the newest Baltic file has not grown for STALE_S seconds.
#
# Run: nohup bash scripts/recorder_watchdog.sh > logs/watchdog.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

REGIONS="BALTIC_WATCH,HORMUZ,MALAYSIA_EOPL,LACONIA,RED_SEA,BLACK_SEA"
STALE_S="${STALE_S:-180}"
UV="${UV:-$HOME/.local/bin/uv}"

start_recorder() {
  pkill -f "record_ais.py --regions" 2>/dev/null
  sleep 2
  [ -f logs/record_all.log ] && cp logs/record_all.log "logs/record_all.$(date +%H%M%S).log"
  nohup caffeinate -is "$UV" run python scripts/record_ais.py --regions "$REGIONS" > logs/record_all.log 2>&1 &
  echo "$(date '+%F %T') started recorder (pid $!)"
}

pgrep -f "record_ais.py --regions" >/dev/null || start_recorder
while true; do
  sleep 60
  newest=$(ls -t data/cache/ais/BALTIC_WATCH/*.jsonl.gz 2>/dev/null | head -1)
  if [ -z "$newest" ] || ! pgrep -f "record_ais.py --regions" >/dev/null; then
    echo "$(date '+%F %T') recorder missing; restarting"; start_recorder; continue
  fi
  age=$(( $(date +%s) - $(stat -f %m "$newest") ))
  if [ "$age" -gt "$STALE_S" ]; then
    echo "$(date '+%F %T') no data for ${age}s; restarting"; start_recorder
  fi
done
