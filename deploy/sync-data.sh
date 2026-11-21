#!/usr/bin/env bash
# Copy the laptop's recording, cache and snapshot to a provisioned Dark Fleet box.
#
# Usage: deploy/sync-data.sh root@IP
#
# Run from the repo root on your laptop, after provision.sh has created /opt/darkfleet.
# Never touches .env: keys live only in /opt/darkfleet/.env, which you scp separately.
# No --delete, so recordings that already exist on the server are never removed.
set -euo pipefail

REMOTE="${1:-}"
if [ -z "$REMOTE" ]; then
	echo "usage: deploy/sync-data.sh root@IP" >&2
	exit 2
fi

DATA_DIR="/opt/darkfleet/data"

if [ ! -d deploy ] || [ ! -f scripts/build_watch.py ]; then
	echo "run this from the repo root: deploy/sync-data.sh $REMOTE" >&2
	exit 2
fi

# macOS ships openrsync, which lacks --info=progress2; fall back to --progress there.
RSYNC=(rsync -az)
if rsync --help 2>&1 | grep -q -- '--info='; then
	RSYNC+=(--info=progress2)
else
	RSYNC+=(--progress)
fi

echo "==> Creating $DATA_DIR on $REMOTE"
ssh "$REMOTE" "mkdir -p '$DATA_DIR/snapshot' '$DATA_DIR/ais' '$DATA_DIR/sanctions'"

copied=()

if [ -d data/snapshot ]; then
	echo "==> data/snapshot/ -> $REMOTE:$DATA_DIR/snapshot/"
	"${RSYNC[@]}" data/snapshot/ "$REMOTE:$DATA_DIR/snapshot/"
	copied+=("snapshot")
else
	echo "==> data/snapshot/ not found, skipping (darkfleet-watch will rebuild it from the AIS files)" >&2
fi

if [ -d data/cache/ais ]; then
	echo "==> data/cache/ais/ -> $REMOTE:$DATA_DIR/ais/"
	"${RSYNC[@]}" data/cache/ais/ "$REMOTE:$DATA_DIR/ais/"
	copied+=("ais")
else
	echo "==> data/cache/ais/ not found, skipping" >&2
fi

if [ -d data/sanctions ]; then
	echo "==> data/sanctions/ -> $REMOTE:$DATA_DIR/sanctions/"
	"${RSYNC[@]}" data/sanctions/ "$REMOTE:$DATA_DIR/sanctions/"
	copied+=("sanctions")
else
	echo "==> data/sanctions/ not found, skipping"
fi

if [ "${#copied[@]}" -eq 0 ]; then
	echo "nothing to copy: no data/snapshot, data/cache/ais or data/sanctions in $(pwd)" >&2
	exit 1
fi

echo "==> Handing $DATA_DIR to darkfleet"
if ssh "$REMOTE" "id -u darkfleet >/dev/null 2>&1"; then
	ssh "$REMOTE" "chown -R darkfleet:darkfleet '$DATA_DIR'"
else
	echo "    darkfleet user does not exist yet; provision.sh will chown $DATA_DIR" >&2
fi

echo
echo "Copied to $REMOTE:$DATA_DIR: ${copied[*]}"
echo "The recorder and the watch builder keep it current from here on."
