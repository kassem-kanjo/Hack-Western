#!/usr/bin/env bash
# One-command Sunday rebuild: snapshot -> Tiger aggregates -> validations -> demo gate -> numbers.
#
#   bash scripts/sunday.sh [--video]
#
# Every step is logged with a UTC timestamp and the script stops at the first failure, so a
# red run never prints SUNDAY READY or leaves a half-current sheet behind. .env is sourced
# with allexport so the recorder/API keys reach the child processes, but it is never echoed.
#
# --video additionally serves web/dist on :8099 and runs media/record.mjs after the demo
# gate; the server is always killed before the script exits.
set -euo pipefail

cd "$(dirname "$0")/.."

UV="${UV:-$HOME/.local/bin/uv}"
SNAPSHOT_DIR="data/snapshot"
VIDEO=0
STATS_TMP=""
VIDEO_PID=""

for arg in "$@"; do
  case "$arg" in
    --video) VIDEO=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p out

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
step() { log "== $*"; }

cleanup() {
  if [ -n "$VIDEO_PID" ]; then
    kill "$VIDEO_PID" 2>/dev/null || true
  fi
  if [ -n "$STATS_TMP" ]; then
    rm -f "$STATS_TMP"
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM
trap 'log "FAILED at line $LINENO: $BASH_COMMAND"' ERR

# Load keys without printing them. An absent .env is fine when the caller exported the vars.
if [ -f .env ]; then
  log "loading .env (contents not echoed)"
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# The four regions the recorder actually wrote AIS for (Hormuz included for the store,
# even though its screen is the GFW radar one).
AIS_REGIONS=(BALTIC_WATCH MALAYSIA_EOPL BLACK_SEA HORMUZ)

# ISO timestamp 30 hours ago, portable across GNU and BSD date.
since_utc() {
  local epoch
  epoch=$(($(date -u +%s) - 30 * 3600))
  if date -u -d "@$epoch" +%Y-%m-%dT%H:%M:%SZ >/dev/null 2>&1; then
    date -u -d "@$epoch" +%Y-%m-%dT%H:%M:%SZ
  else
    date -u -r "$epoch" +%Y-%m-%dT%H:%M:%SZ
  fi
}

step "1/6 rebuild every snapshot dataset"
make snapshot

if [ -n "${DATABASE_URL:-}" ]; then
  step "2/6 load new AIS into Tiger and merge the aggregates"
  SINCE="$(since_utc)"
  log "AIS window starts at $SINCE"
  for region in "${AIS_REGIONS[@]}"; do
    directory="data/cache/ais/$region"
    if [ -d "$directory" ]; then
      log "loading $region from $directory"
      "$UV" run python scripts/load_ais.py --dir "$directory" --since "$SINCE"
    else
      log "skipping $region: no recording at $directory"
    fi
  done
  STATS_TMP="$(mktemp "${TMPDIR:-/tmp}/darkfleet-stats.XXXXXX")"
  "$UV" run python scripts/export_stats.py --output "$STATS_TMP"
  "$UV" run python scripts/numbers.py --merge-stats "$STATS_TMP"
  rm -f "$STATS_TMP"
  STATS_TMP=""
else
  step "2/6 DATABASE_URL not set: keeping the existing aggregates"
fi

step "3/6 validate tracks and diagnose recall on the Baltic recording"
env -u DATABASE_URL "$UV" run python scripts/validate_tracks.py \
  --jsonl-dir data/cache/ais/BALTIC_WATCH
env -u DATABASE_URL "$UV" run python scripts/diagnose_recall.py \
  --jsonl-dir data/cache/ais/BALTIC_WATCH

step "4/6 demo gate"
make demo-check

if [ "$VIDEO" -eq 1 ]; then
  step "5/6 record the demo video"
  DARKFLEET_WEB_DIST=web/dist DARKFLEET_DATA="$SNAPSHOT_DIR" \
    "$UV" run uvicorn darkfleet.api.app:app --port 8099 >out/demo-video-server.log 2>&1 &
  VIDEO_PID=$!
  for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8099/api/health >/dev/null 2>&1; then
      break
    fi
    if [ "$i" -eq 30 ]; then
      log "API not healthy after 30 s (see out/demo-video-server.log)"
      exit 1
    fi
    sleep 1
  done
  (cd media && node record.mjs --base http://127.0.0.1:8099/ --out out/demo-v1)
  kill "$VIDEO_PID" 2>/dev/null || true
  VIDEO_PID=""
else
  step "5/6 video skipped (pass --video to record it)"
fi

step "6/6 write the numbers sheet"
"$UV" run python scripts/numbers.py

log "SUNDAY READY"
printf 'out/numbers.md\n'
