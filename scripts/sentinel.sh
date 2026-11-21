#!/usr/bin/env bash
# Sentinel: cheap checks of everything that can stop silently, every few minutes.
#
# Why: a data path can die without an error anywhere (Tiger stopped receiving pings after one
# load and nobody noticed for hours). Each check is a file age, an HTTP code or one SQL
# count, so a pass costs a few seconds and no model tokens. Only changes are printed (a
# check turning RED, or recovering), so a watcher wakes someone only when it matters.
#
# Run once:     bash scripts/sentinel.sh            (prints every check, exit 1 if any RED)
# Run forever:  bash scripts/sentinel.sh --loop 300 (prints only changes; full log in logs/sentinel.log)
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
API="${SENTINEL_API:-http://127.0.0.1:8000}"
STATE=logs/sentinel.state
mkdir -p logs

# Minutes since a file was last written, or 9999 when it is missing.
age_min() { [ -e "$1" ] || { echo 9999; return; }; echo $(( ($(date +%s) - $(stat -f %m "$1" 2>/dev/null || stat -c %Y "$1")) / 60 )); }

check() { # name, status (OK|RED|WARN), detail
  printf '%s\t%s\t%s\n' "$1" "$2" "$3"
}

run_checks() {
  # 1. Recorder: the busiest region's newest hour file grew in the last 3 min.
  local f a
  f=$(ls -t data/cache/ais/BALTIC_WATCH/*.jsonl.gz 2>/dev/null | head -1)
  a=$(age_min "${f:-/nonexistent}")
  if [ "$a" -le 3 ]; then check recorder OK "writing ($(basename "$f"))"
  else check recorder RED "no new AIS data for ${a} min"; fi

  # 2. Alert loop: watch.json rebuilt in the last 3 min and not empty.
  a=$(age_min data/snapshot/watch.json)
  if [ "$a" -gt 3 ]; then check watch-loop RED "watch.json not rebuilt for ${a} min"
  else
    local n
    n=$(python3 -c "import json;d=json.load(open('data/snapshot/watch.json'));print(len(d.get('ships',[])))" 2>/dev/null || echo 0)
    if [ "${n:-0}" -gt 0 ]; then check watch-loop OK "fresh, ${n} ships"; else check watch-loop RED "watch.json has 0 ships"; fi
  fi

  # 3. API: every route the screens depend on answers 200.
  local bad="" u code
  for u in /api/health /api/watch /api/areas/stats /api/hormuz/trend /api/sar/scan /api/edge/status; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$API$u")
    [ "$code" = 200 ] || bad="$bad $u=$code"
  done
  if [ -z "$bad" ]; then check api OK "6 routes 200"; else check api RED "failing:$bad"; fi

  # 3b. Live numbers: the Baltic rate the app shows is non-zero while the recorder writes.
  #     (Tiger once served 0 msgs/min for every area with every route answering 200.)
  local rate
  rate=$(curl -s --max-time 8 "$API/api/areas/stats" | python3 -c "
import json,sys
d=json.load(sys.stdin)
a=next((a for a in d.get('areas',[]) if a.get('id')=='BALTIC_WATCH'),{})
print(a.get('msgs_per_min',0), a.get('source','?'))" 2>/dev/null)
  case "$rate" in
    ""|0|0.0*) check live-rate RED "Baltic msgs/min reads 0 in the app (${rate:-no answer})";;
    *) check live-rate OK "Baltic ${rate% *} msgs/min from ${rate#* }";;
  esac

  # 4. App build: web/dist is newer than the newest web source on this checkout.
  local src dist
  src=$(find web/src -type f -newer web/dist/index.html 2>/dev/null | head -1)
  if [ ! -f web/dist/index.html ]; then check app-build RED "web/dist missing"
  elif [ -n "$src" ]; then check app-build WARN "web/dist older than $src (rebuild + restart)"
  else check app-build OK "current"; fi

  # 5. Patrol unit (QNX Pi): reachable and its detector alive, as the API sees it.
  local edge
  edge=$(curl -s --max-time 8 "$API/api/edge/status" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('unreadable'); raise SystemExit
print(d.get('state') or d.get('verdict') or ('online' if d.get('online', True) else 'offline'))" 2>/dev/null)
  case "$edge" in ""|unreadable|offline|UNKNOWN|unknown) check pi WARN "patrol unit: ${edge:-no answer}";;
                  *) check pi OK "patrol unit: $edge";; esac

  # 6. Tiger Data: positions received a ping in the last 15 min (live store, #208).
  local tiger
  tiger=$(uv run --no-sync python - 2>/dev/null <<'PY'
import datetime as dt
url = next((l.split("=", 1)[1].strip().strip("'\"") for l in open(".env") if l.startswith("DATABASE_URL=")), "")
if not url:
    print("no-url"); raise SystemExit
import psycopg
with psycopg.connect(url, connect_timeout=8) as c:
    n, last = c.execute("select count(*), max(ts) from positions where ts > now() - interval '1 day'").fetchone()
age = int((dt.datetime.now(dt.UTC) - last).total_seconds() // 60) if last else 99999
print(f"{age} {n}")
PY
)
  case "$tiger" in
    "") check tiger RED "unreachable";;
    no-url) check tiger WARN "DATABASE_URL not set";;
    *) set -- $tiger
       if [ "$1" -le 15 ]; then check tiger OK "last ping ${1} min ago, ${2} in 24 h"
       else check tiger RED "no ping stored for ${1} min (sink down?)"; fi;;
  esac

  # 7. Delegated coders: a log untouched for 30 min with no summary.json is a dead lane.
  local oc dead=""
  oc=$(grep -s '^OC_OUT=' ~/.claude/machine.env | cut -d= -f2)
  if [ -n "$oc" ] && [ -d "$oc" ]; then
    for d in "$oc"/*/; do
      local name log
      name=$(basename "$d"); log="$oc/$name.log"
      [ -f "$d/summary.json" ] && continue
      [ -f "$log" ] || continue
      a=$(age_min "$log")
      [ "$a" -gt 30 ] && [ "$a" -lt 600 ] && dead="$dead $name"
    done
  fi
  if [ -z "$dead" ]; then check lanes OK "no dead lanes"; else check lanes RED "dead lanes:$dead"; fi

  # 8. Disk: at least 5 GB free (recordings and worktrees grow overnight).
  local free
  free=$(df -g . | awk 'NR==2{print $4}')
  if [ "${free:-0}" -ge 5 ]; then check disk OK "${free} GB free"; else check disk RED "only ${free} GB free"; fi
}

once() {
  local out red=0
  out=$(run_checks)
  printf '%s\n' "$out" | awk -F'\t' '{printf "%-6s %-11s %s\n", $2, $1, $3}'
  printf '%s\n' "$out" | grep -q $'\tRED\t' && red=1
  return $red
}

loop() {
  local every="$1" out prev line name status
  touch "$STATE"
  while true; do
    out=$(run_checks)
    { date '+%F %T'; printf '%s\n' "$out"; } >> logs/sentinel.log
    while IFS=$'\t' read -r name status detail; do
      prev=$(grep -s "^$name"$'\t' "$STATE" | cut -f2)
      if [ "$status" != "${prev:-OK}" ] && [ "$status" != WARN -o "${prev:-OK}" = RED ]; then
        echo "$(date '+%H:%M') SENTINEL $status $name: $detail"
      fi
    done <<< "$out"
    printf '%s\n' "$out" > "$STATE"
    sleep "$every"
  done
}

case "${1:-}" in
  --loop) loop "${2:-300}";;
  *) once;;
esac
