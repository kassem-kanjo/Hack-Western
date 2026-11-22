#!/usr/bin/env bash
# Deploy the edge unit to a QNX Raspberry Pi over key-based SSH.
#
# Usage: bash scripts/edge_deploy.sh <user>@<pi-ip> [--camera | --qnxcam UNIT[:flip]]
#
# Copies src/darkfleet/edge and the YOLOX-nano ONNX model to ~/darkfleet on the
# Pi, checks the Python/ONNX runtime, then starts the supervisor (and the tiny
# status HTTP server) in the background.
#
# --qnxcam 5 builds edge/qnxcam/qnxcam.c on the Pi and runs the USB webcam through
# it (--qnxcam 5:flip rotates the picture 180 degrees). --lat/--lon/--patrol-name
# set the unit's position and label, and --ais FILE copies an AIS snapshot built by
# scripts/make_patrol_ais.py.
#
# Only ssh and scp are used; if scp is unavailable the files go through
# `tar | ssh` (QNX often has no rsync). Passwords are never handled: every ssh
# call uses BatchMode, so a key-less target fails with instructions instead of
# prompting.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# BatchMode also refuses a password prompt, which is the point: no passwords here.
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
REMOTE_DIR="darkfleet"
REMOTE_SOURCE="net:7070"
# The Pi must be reachable from the laptop (camera frames) and the console, so the deploy
# opens both listeners deliberately. Tokens below still gate them when configured.
REMOTE_BIND="0.0.0.0"

# Optional deployment extras, set by the flags below.
QNX_ARG=""
ACT_LED=""
NO_GUARD=""
PATROL_LAT=""
PATROL_LON=""
PATROL_NAME=""
AIS_SRC=""

# Quote a value for the remote shell without ever printing it: tokens may contain quotes.
shell_quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

usage() {
    echo "usage: bash scripts/edge_deploy.sh <user>@<pi-ip> [options]" >&2
    echo "  --camera             use camera:0 on the Pi instead of net:7070" >&2
    echo "  --qnxcam UNIT[:flip]  QNX USB webcam through qnxcam (unit 5, optional 180 flip)" >&2
    echo "  --lat N --lon N       unit position (patrol verdict radius)" >&2
    echo "  --act-led             drive the Pi 5 ACT LED from the alarm (solid = alert, blink = blind)" >&2
    echo "  --no-guard            A/B baseline: no priority guard" >&2
    echo "  --patrol-name NAME    label for the patrol position" >&2
    echo "  --ais FILE            AIS snapshot JSON copied to the Pi" >&2
}

TARGET=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --camera)
            REMOTE_SOURCE="camera:0"
            shift
            ;;
        --qnxcam)
            if [ "$#" -lt 2 ]; then
                echo "--qnxcam needs UNIT[:flip]" >&2
                exit 2
            fi
            QNX_ARG="$2"
            shift 2
            ;;
        --act-led)
            ACT_LED=1
            shift
            ;;
        --no-guard)
            NO_GUARD=1
            shift
            ;;
        --lat|--lon|--patrol-name|--ais)
            if [ "$#" -lt 2 ]; then
                echo "$1 needs a value" >&2
                exit 2
            fi
            case "$1" in
                --lat) PATROL_LAT="$2" ;;
                --lon) PATROL_LON="$2" ;;
                --patrol-name) PATROL_NAME="$2" ;;
                --ais) AIS_SRC="$2" ;;
            esac
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "unknown option: $1" >&2
            usage
            exit 2
            ;;
        *)
            if [ -n "$TARGET" ]; then
                echo "unexpected argument: $1" >&2
                usage
                exit 2
            fi
            TARGET="$1"
            shift
            ;;
    esac
done

# Parse --qnxcam once, so the supervisor and the detector source agree on the flip.
QNX_UNIT=""
QNX_SOURCE=""
QNX_SUPERVISOR=""
if [ -n "$QNX_ARG" ]; then
    QNX_UNIT="${QNX_ARG%%:*}"
    QNX_MODE=""
    if [ "$QNX_UNIT" != "$QNX_ARG" ]; then
        QNX_MODE="${QNX_ARG#*:}"
    fi
    if ! printf '%s' "$QNX_UNIT" | grep -Eq '^[0-9]+$'; then
        echo "--qnxcam needs a numeric camera unit, got '$QNX_ARG'" >&2
        exit 2
    fi
    case "$QNX_MODE" in
        "") QNX_SOURCE="qnxfile:/tmp/qnxcam.frame" ;;
        flip) QNX_SOURCE="qnxfile:/tmp/qnxcam.frame:flip" ;;
        *)
            echo "--qnxcam accepts UNIT or UNIT:flip, got '$QNX_ARG'" >&2
            exit 2
            ;;
    esac
    QNX_SUPERVISOR="$QNX_UNIT:/tmp/qnxcam.frame"
    REMOTE_SOURCE="$QNX_SOURCE"
fi

if [ -z "$TARGET" ] || [ "${TARGET#*@}" = "$TARGET" ]; then
    echo "expected a target of the form user@host, got ${TARGET:-nothing}" >&2
    usage
    exit 2
fi

# The status server is always started and serves frames and sightings over the network, so
# refuse before copying anything: a half-deployed unit with an open status port is worse.
if [ -z "${EDGE_STATUS_TOKEN:-}" ]; then
    echo "EDGE_STATUS_TOKEN is not set; refusing to start the status server without it." >&2
    echo "         export EDGE_STATUS_TOKEN=<a long random string>, then deploy again." >&2
    echo "         The API needs the same value in its environment." >&2
    exit 2
fi

if ! ssh "${SSH_OPTS[@]}" "$TARGET" true; then
    echo "cannot log in to $TARGET with an SSH key." >&2
    echo "This script never handles passwords; run this once, then retry:" >&2
    echo "    ssh-copy-id $TARGET" >&2
    exit 3
fi

# --- local model ------------------------------------------------------------

MODEL_SRC="$REPO_ROOT/data/models/yolox_nano.onnx"
if [ ! -f "$MODEL_SRC" ] && [ -f "$REPO_ROOT/models/yolox_nano.onnx" ]; then
    MODEL_SRC="$REPO_ROOT/models/yolox_nano.onnx"
fi
if [ ! -f "$MODEL_SRC" ]; then
    echo "model missing; fetching it with scripts/fetch_edge_model.sh"
    if ! bash "$SCRIPT_DIR/fetch_edge_model.sh" --dest "$REPO_ROOT/models"; then
        echo "could not fetch the model; run scripts/fetch_edge_model.sh and retry" >&2
        exit 4
    fi
    MODEL_SRC="$REPO_ROOT/models/yolox_nano.onnx"
fi
if [ ! -f "$MODEL_SRC" ]; then
    echo "model still missing at $MODEL_SRC" >&2
    exit 4
fi

# The Pi runs the 320 px variant: ~100 ms per frame instead of ~180 ms (200 ms deadline).
INPUT_SIZE=320
MODEL320="$(dirname "$MODEL_SRC")/yolox_nano_320.onnx"
if [ ! -f "$MODEL320" ]; then
    echo "building the 320 px model with scripts/resize_edge_model.py"
    if ! (cd "$REPO_ROOT" && uv run --extra edge --with onnx python scripts/resize_edge_model.py "$MODEL_SRC" "$MODEL320" "$INPUT_SIZE"); then
        echo "could not build the 320 px model; falling back to 416 px" >&2
        MODEL320=""
        INPUT_SIZE=416
    fi
fi
if [ -n "$MODEL320" ]; then
    MODEL_SRC="$MODEL320"
fi
REMOTE_MODEL="yolox_nano_${INPUT_SIZE}.onnx"
[ "$INPUT_SIZE" = "416" ] && REMOTE_MODEL="yolox_nano.onnx"

# --- copy -------------------------------------------------------------------

echo "copying edge code and model to $TARGET:~/$REMOTE_DIR"
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p \$HOME/$REMOTE_DIR/src/darkfleet \$HOME/$REMOTE_DIR/models"

copy_tree() {
    local src="$1" dest="$2"
    if command -v scp >/dev/null 2>&1 && scp -r "${SSH_OPTS[@]}" "$src" "$TARGET:$dest"; then
        return 0
    fi
    echo "scp unavailable or failed; using tar | ssh" >&2
    tar -C "$(dirname "$src")" -cf - "$(basename "$src")" |
        ssh "${SSH_OPTS[@]}" "$TARGET" "tar -xf - -C $dest"
}

copy_file() {
    local src="$1" dest="$2"
    if command -v scp >/dev/null 2>&1 && scp "${SSH_OPTS[@]}" "$src" "$TARGET:$dest"; then
        return 0
    fi
    echo "scp unavailable or failed; using cat | ssh" >&2
    cat "$src" | ssh "${SSH_OPTS[@]}" "$TARGET" "cat > $dest"
}

copy_tree "$REPO_ROOT/src/darkfleet/edge" "$REMOTE_DIR/src/darkfleet"
copy_file "$MODEL_SRC" "$REMOTE_DIR/models/$REMOTE_MODEL"

# --- optional webcam helper and AIS snapshot --------------------------------

if [ -n "$QNX_ARG" ]; then
    echo "copying qnxcam.c and building it on $TARGET"
    copy_file "$REPO_ROOT/edge/qnxcam/qnxcam.c" "$REMOTE_DIR/qnxcam.c"
    if ! ssh "${SSH_OPTS[@]}" "$TARGET" 'command -v gcc >/dev/null 2>&1'; then
        echo "gcc is not installed on $TARGET; install the QNX toolchain (gcc) and retry" >&2
        exit 6
    fi
    if ! ssh "${SSH_OPTS[@]}" "$TARGET" "cd \$HOME/$REMOTE_DIR && gcc -O2 -o qnxcam qnxcam.c -lcamapi"; then
        echo "building qnxcam failed; libcamapi headers must be available on the Pi" >&2
        exit 7
    fi
fi

if [ -n "$AIS_SRC" ]; then
    if [ ! -f "$AIS_SRC" ]; then
        echo "AIS snapshot missing: $AIS_SRC (build one with scripts/make_patrol_ais.py)" >&2
        exit 8
    fi
    echo "copying AIS snapshot to $TARGET:~/$REMOTE_DIR/patrol_ais.json"
    copy_file "$AIS_SRC" "$REMOTE_DIR/patrol_ais.json"
fi

# --- readiness --------------------------------------------------------------

echo "== readiness on $TARGET =="
if ! ssh "${SSH_OPTS[@]}" "$TARGET" 'uname -a; python3 --version; python3 -c "import numpy, cv2; import importlib.util as u; print(\"numpy\", numpy.__version__, \"cv2\", cv2.__version__, \"(onnxruntime or OpenCV DNN will run the model)\")"'; then
    echo "readiness check failed; on the Pi run: sudo apk add python3-numpy python3-opencv (python3-onnxruntime optional)" >&2
    exit 5
fi

# --- start ------------------------------------------------------------------

echo "starting supervisor with --source $REMOTE_SOURCE"
# Forward the optional shared tokens when the deploying shell has them. The values are
# single-quoted for the remote shell and never echoed here.
NET_ENV="EDGE_NET_BIND=$(shell_quote "$REMOTE_BIND")"
STATUS_ENV=""
if [ -n "${EDGE_NET_TOKEN:-}" ]; then
    NET_ENV="$NET_ENV EDGE_NET_TOKEN=$(shell_quote "$EDGE_NET_TOKEN")"
fi
if [ -n "${EDGE_STATUS_TOKEN:-}" ]; then
    STATUS_ENV="EDGE_STATUS_TOKEN=$(shell_quote "$EDGE_STATUS_TOKEN")"
fi

# Supervisor extras. Positions are numbers; the patrol name is single-quoted for the remote shell.
SUPERVISOR_ARGS=""
if [ -n "$PATROL_LAT" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --lat $PATROL_LAT"
fi
if [ -n "$PATROL_LON" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --lon $PATROL_LON"
fi
if [ -n "$PATROL_NAME" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --patrol-name $(shell_quote "$PATROL_NAME")"
fi
if [ -n "$ACT_LED" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --act-led"
fi
if [ -n "$NO_GUARD" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --no-guard"
fi
if [ -n "$AIS_SRC" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --ais \$HOME/$REMOTE_DIR/patrol_ais.json"
fi
if [ -n "$QNX_SUPERVISOR" ]; then
    SUPERVISOR_ARGS="$SUPERVISOR_ARGS --qnxcam $QNX_SUPERVISOR --qnxcam-bin \$HOME/$REMOTE_DIR/qnxcam"
fi

ssh "${SSH_OPTS[@]}" "$TARGET" "
    cd \$HOME/$REMOTE_DIR
    if [ -f supervisor.pid ]; then kill \$(cat supervisor.pid) 2>/dev/null || true; fi
    if [ -f status_http.pid ]; then kill \$(cat status_http.pid) 2>/dev/null || true; fi
    # The old supervisor stops its children (safety core, detector, camera) on SIGTERM. Wait for
    # it: a late safety core would delete the new one's socket, and the old camera helper would
    # still hold the USB camera ("camera_open failed: 16").
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        kill -0 \$(cat supervisor.pid 2>/dev/null) 2>/dev/null || break
        sleep 0.5
    done
    kill -9 \$(cat supervisor.pid 2>/dev/null) 2>/dev/null || true
    sleep 1
    PYTHONPATH=\$HOME/$REMOTE_DIR/src $NET_ENV nohup python3 -m darkfleet.edge.supervisor \
        --source $REMOTE_SOURCE \
        --model \$HOME/$REMOTE_DIR/models/$REMOTE_MODEL --input-size $INPUT_SIZE \
        --state-dir \$HOME/$REMOTE_DIR$SUPERVISOR_ARGS \
        > \$HOME/$REMOTE_DIR/supervisor.log 2>&1 &
    echo \$! > \$HOME/$REMOTE_DIR/supervisor.pid
    PYTHONPATH=\$HOME/$REMOTE_DIR/src $STATUS_ENV nohup python3 -m darkfleet.edge.status_http \
        --status \$HOME/$REMOTE_DIR/edge_status.json --bind $REMOTE_BIND --port 7071 \
        > \$HOME/$REMOTE_DIR/status_http.log 2>&1 &
    echo \$! > \$HOME/$REMOTE_DIR/status_http.pid
    sleep 1
    echo \"started supervisor pid \$(cat supervisor.pid), status_http pid \$(cat status_http.pid)\"
"

cat <<EOF
deployed to $TARGET
  status:  ~/$REMOTE_DIR/edge_status.json (HTTP on port 7071)
  logs:    ~/$REMOTE_DIR/supervisor.log, ~/$REMOTE_DIR/status_http.log
  stop:    ssh $TARGET 'kill \$(cat ~/$REMOTE_DIR/supervisor.pid) \$(cat ~/$REMOTE_DIR/status_http.pid)'
  console: EDGE_HOST=<pi-ip> in the API environment
EOF
if [ -n "${EDGE_STATUS_TOKEN:-}" ]; then
    echo "         EDGE_STATUS_TOKEN=<same value> in the API environment" >&2
fi
if [ -n "${EDGE_NET_TOKEN:-}" ]; then
    echo "         EDGE_NET_TOKEN=<same value> is required as camera_bridge's first message" >&2
fi
