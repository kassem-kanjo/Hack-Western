#!/usr/bin/env bash
# Deploy the edge unit to a QNX Raspberry Pi over key-based SSH.
#
# Usage: bash scripts/edge_deploy.sh <user>@<pi-ip> [--camera]
#
# Copies src/darkfleet/edge and the YOLOX-nano ONNX model to ~/darkfleet on the
# Pi, checks the Python/ONNX runtime, then starts the supervisor (and the tiny
# status HTTP server) in the background.
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

# Quote a value for the remote shell without ever printing it: tokens may contain quotes.
shell_quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

usage() {
    echo "usage: bash scripts/edge_deploy.sh <user>@<pi-ip> [--camera]" >&2
    echo "  --camera   use camera:0 on the Pi instead of net:7070" >&2
}

TARGET=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --camera)
            REMOTE_SOURCE="camera:0"
            shift
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

if [ -z "$TARGET" ] || [ "${TARGET#*@}" = "$TARGET" ]; then
    echo "expected a target of the form user@host, got ${TARGET:-nothing}" >&2
    usage
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
copy_file "$MODEL_SRC" "$REMOTE_DIR/models/yolox_nano.onnx"

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
ssh "${SSH_OPTS[@]}" "$TARGET" "
    cd \$HOME/$REMOTE_DIR
    if [ -f supervisor.pid ]; then kill \$(cat supervisor.pid) 2>/dev/null || true; fi
    if [ -f status_http.pid ]; then kill \$(cat status_http.pid) 2>/dev/null || true; fi
    PYTHONPATH=\$HOME/$REMOTE_DIR/src $NET_ENV nohup python3 -m darkfleet.edge.supervisor \
        --source $REMOTE_SOURCE \
        --model \$HOME/$REMOTE_DIR/models/yolox_nano.onnx \
        --state-dir \$HOME/$REMOTE_DIR \
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
