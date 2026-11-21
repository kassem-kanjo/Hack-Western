#!/usr/bin/env bash
# Run the priority-guard A/B on the QNX Pi, then put the normal unit back.
#
# Usage: bash scripts/qnx_ab_run.sh <user>@<pi-ip> <same options you deploy with...>
#   e.g. bash scripts/qnx_ab_run.sh qnxuser@172.23.44.244 --qnxcam 5:flip --lat 54.45 --lon 10.25 \
#            --patrol-name "Kiel Bight" --ais data/edge/patrol_ais.json --act-led
#
# 1. stops the running unit (the test needs the CPU and the model to itself),
# 2. copies the ship photo (data/demo_ship_min.jpg) and runs darkfleet.edge.ab_test on the Pi:
#    two arms (no guard, guard), each a real supervisor on that photo with 10 s of runaway load,
# 3. redeploys the unit with the options you passed. The result is ~/darkfleet/ab_result.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${1:?usage: qnx_ab_run.sh user@host [deploy options]}"
shift
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)

PHOTO="$REPO_ROOT/data/demo_ship_min.jpg"
if [ ! -f "$PHOTO" ]; then
    echo "missing $PHOTO (a ship photo: see docs/patrol-demo.md)" >&2
    exit 2
fi
AIS="$REPO_ROOT/data/edge/patrol_ais.json"
LAT=""
LON=""
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
    case "${ARGS[$i]}" in
        --lat) LAT="${ARGS[$((i + 1))]}" ;;
        --lon) LON="${ARGS[$((i + 1))]}" ;;
        --ais) AIS="${ARGS[$((i + 1))]}" ;;
    esac
done
[ -n "$LAT" ] && [ -n "$LON" ] || { echo "pass --lat and --lon as for the deploy" >&2; exit 2; }

echo "stopping the unit"
ssh "${SSH_OPTS[@]}" "$TARGET" 'cd darkfleet 2>/dev/null && { kill $(cat supervisor.pid) $(cat status_http.pid) 2>/dev/null || true; sleep 4; }'
ssh "${SSH_OPTS[@]}" "$TARGET" 'mkdir -p $HOME/darkfleet/ab/frames'
scp -q "${SSH_OPTS[@]}" "$PHOTO" "$TARGET:darkfleet/ab/frames/ship.jpg"
scp -q "${SSH_OPTS[@]}" "$AIS" "$TARGET:darkfleet/ab/patrol_ais.json"

echo "running the A/B (about 3 minutes)"
ssh "${SSH_OPTS[@]}" "$TARGET" "cd \$HOME/darkfleet && PYTHONPATH=\$HOME/darkfleet/src python3 -m darkfleet.edge.ab_test \
    --frames \$HOME/darkfleet/ab/frames --ais \$HOME/darkfleet/ab/patrol_ais.json \
    --model \$HOME/darkfleet/models/yolox_nano_320.onnx --input-size 320 --lat $LAT --lon $LON \
    --work-dir \$HOME/darkfleet/ab --out \$HOME/darkfleet/ab_result.json" || echo "A/B failed; redeploying anyway" >&2

echo "redeploying the unit"
bash "$SCRIPT_DIR/edge_deploy.sh" "$TARGET" "$@"
ssh "${SSH_OPTS[@]}" "$TARGET" 'cat $HOME/darkfleet/ab_result.json'
