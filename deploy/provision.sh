#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 VPS to serve Dark Fleet: Caddy on 80/443 in front of
# uvicorn (API + built SPA) and the AIS recorder, both as the unprivileged darkfleet user.
#
# Usage: sudo DOMAIN=demo.example.com bash deploy/provision.sh
#        sudo bash deploy/provision.sh          # plain IP on port 80
#
# Idempotent: safe to re-run after scp-ing .env or pulling new code. It never creates,
# prints or modifies the contents of .env; it only requires that the file already exists.
set -euo pipefail

REPO_URL="https://github.com/kassem-kanjo/Hack-Western"
APP_DIR="/opt/darkfleet"
APP_USER="darkfleet"
APP_HOME="/home/darkfleet"
DOMAIN="${DOMAIN:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
	echo "run as root: sudo DOMAIN=your.domain bash deploy/provision.sh" >&2
	exit 1
fi

if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
	echo "DOMAIN must be a bare hostname (letters, digits, dots, dashes), got: $DOMAIN" >&2
	exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> Installing base packages"
apt-get update -y
apt-get install -y --no-install-recommends \
	ca-certificates curl git gnupg rsync apt-transport-https debian-keyring debian-archive-keyring

echo "==> Adding the official Caddy apt repository"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
	| gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
	-o /etc/apt/sources.list.d/caddy-stable.list
apt-get update -y
apt-get install -y caddy

echo "==> Installing Node.js 22 (NodeSource)"
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
	curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
	apt-get install -y nodejs
fi

echo "==> Installing uv"
if ! command -v uv >/dev/null 2>&1; then
	curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

echo "==> Creating the $APP_USER user and $APP_DIR"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
	useradd --create-home --home-dir "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR"

echo "==> Fetching Dark Fleet"
if [ -d "$APP_DIR/.git" ]; then
	chown -R "$APP_USER:$APP_USER" "$APP_DIR"
	runuser -u "$APP_USER" -- git -C "$APP_DIR" pull --ff-only
else
	tmp_dir="$(mktemp -d)"
	git clone --depth 1 "$REPO_URL" "$tmp_dir/repo"
	# Copy into place instead of cloning straight in: .env may already have been scp-ed.
	cp -a "$tmp_dir/repo/." "$APP_DIR/"
	rm -rf "$tmp_dir"
	chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

if [ ! -f "$APP_DIR/.env" ]; then
	echo "copy .env to /opt/darkfleet/.env (scp .env root@IP:/opt/darkfleet/.env) then re-run" >&2
	exit 1
fi
# The service user must read the keys; nobody else may.
chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

echo "==> Preparing data directories"
mkdir -p "$APP_DIR/data/snapshot" "$APP_DIR/data/ais"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data"

run_as_app() {
	runuser -u "$APP_USER" -- env HOME="$APP_HOME" "$@"
}

echo "==> Installing Python dependencies (uv sync)"
(
	cd "$APP_DIR"
	run_as_app /usr/local/bin/uv sync
)

echo "==> Building the web console"
(
	cd "$APP_DIR/web"
	run_as_app npm ci
	run_as_app npm run build
)

echo "==> Installing systemd units"
install -m 644 "$SCRIPT_DIR/darkfleet-api.service" /etc/systemd/system/darkfleet-api.service
install -m 644 "$SCRIPT_DIR/darkfleet-recorder.service" /etc/systemd/system/darkfleet-recorder.service
systemctl daemon-reload

echo "==> Configuring Caddy"
# The stock caddy.service reads no environment file, so bake the site address into the
# config itself (DOMAIN was validated above as a bare hostname, so sed is safe).
SITE="${DOMAIN:-:80}"
sed "s/{\$DOMAIN::80}/$SITE/" "$SCRIPT_DIR/Caddyfile" >/etc/caddy/Caddyfile
chmod 644 /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo "==> Starting services"
systemctl enable --now darkfleet-api.service
systemctl enable --now darkfleet-recorder.service
systemctl restart darkfleet-api.service darkfleet-recorder.service

echo
echo "Done."
echo "  App:      http://${DOMAIN:-<this-ip>}/"
echo "  API:      curl -s http://127.0.0.1:8000/api/health"
echo "  Logs:     journalctl -u darkfleet-api -f   /   journalctl -u darkfleet-recorder -f"
echo "  Snapshot: rsync -av data/snapshot/ root@<ip>:/opt/darkfleet/data/snapshot/"
