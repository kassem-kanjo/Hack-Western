#!/usr/bin/env bash
# Fetch the YOLOX-nano ONNX detector used by the edge unit.
#
# Usage: bash scripts/fetch_edge_model.sh [--dest <dir>] [--sha256 <expected>]
#
# The download is verified before it is moved into place, so a failed or
# interrupted fetch never leaves a partial file at the destination.
# Exit codes: 0 success, 1 download/verification failure, 2 bad usage.
set -euo pipefail

# YOLOX-nano, Apache-2.0. Copyright (c) Megvii, Inc. and its affiliates.
# https://github.com/Megvii-BaseDetection/YOLOX
MODEL_URL="https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
MODEL_NAME="yolox_nano.onnx"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$REPO_ROOT/data/models"
EXPECTED_SHA=""

usage() {
    echo "usage: bash scripts/fetch_edge_model.sh [--dest <dir>] [--sha256 <expected>]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dest)
            if [ "$#" -lt 2 ]; then
                usage >&2
                exit 2
            fi
            DEST="$2"
            shift 2
            ;;
        --sha256)
            if [ "$#" -lt 2 ]; then
                usage >&2
                exit 2
            fi
            EXPECTED_SHA="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

TMP_DIR="$(mktemp -d)" || {
    echo "could not create a temporary directory" >&2
    exit 1
}
trap 'rm -rf "$TMP_DIR"' EXIT

TMP_FILE="$TMP_DIR/$MODEL_NAME"

# Nothing downloaded here is ever executed: it is only hashed and moved.
if ! curl -fL --retry 2 --connect-timeout 20 -o "$TMP_FILE" "$MODEL_URL"; then
    rm -f "$TMP_FILE"
    echo "download failed; run the detector with --mock instead" >&2
    exit 1
fi

if [ ! -s "$TMP_FILE" ]; then
    rm -f "$TMP_FILE"
    echo "download failed (empty file); run the detector with --mock instead" >&2
    exit 1
fi

if command -v shasum >/dev/null 2>&1; then
    SHA="$(shasum -a 256 "$TMP_FILE" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
    SHA="$(sha256sum "$TMP_FILE" | awk '{print $1}')"
else
    rm -f "$TMP_FILE"
    echo "no SHA-256 tool found (need shasum or sha256sum)" >&2
    exit 1
fi
SHA="$(printf '%s' "$SHA" | tr 'A-Z' 'a-z')"
SIZE="$(wc -c < "$TMP_FILE" | tr -d ' ')"

echo "sha256: $SHA"
echo "bytes:  $SIZE"

if [ -n "$EXPECTED_SHA" ]; then
    EXPECTED_LOWER="$(printf '%s' "$EXPECTED_SHA" | tr 'A-Z' 'a-z')"
    if [ "$SHA" != "$EXPECTED_LOWER" ]; then
        rm -f "$TMP_FILE"
        echo "sha256 mismatch: expected $EXPECTED_LOWER, got $SHA" >&2
        exit 1
    fi
fi

mkdir -p "$DEST"
if ! mv "$TMP_FILE" "$DEST/$MODEL_NAME"; then
    rm -f "$DEST/$MODEL_NAME"
    echo "could not move the model into $DEST" >&2
    exit 1
fi

echo "model:  $DEST/$MODEL_NAME"
echo "sha256: $SHA"
