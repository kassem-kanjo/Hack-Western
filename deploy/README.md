# Deploy Dark Fleet on one VPS

One Ubuntu 24.04 box runs everything. Caddy listens on port 80/443. uvicorn serves the API and the
built web console from the same process. systemd restarts both services if they crash.

## 1. Create the server

In Vultr, deploy **Ubuntu 24.04**, **1 vCPU / 2 GB RAM**, in the region closest to you. Add your SSH
key. Note the public IP address.

Open ports 22, 80 and 443 in the Vultr firewall (or leave the firewall off for the demo).

## 2. Point the domain at it

In GoDaddy, open the DNS zone for your domain. Add an **A** record:

- Name: `@` for the bare domain, or `demo` for `demo.example.com`
- Value: the Vultr IP address
- TTL: 600 seconds

Wait a few minutes. Check with `dig +short demo.example.com`.

## 3. Copy the secrets

From your laptop, in the repo:

```sh
scp .env root@YOUR_IP:/opt/darkfleet/.env
```

If `/opt/darkfleet` does not exist yet, create it first:

```sh
ssh root@YOUR_IP 'mkdir -p /opt/darkfleet'
scp .env root@YOUR_IP:/opt/darkfleet/.env
```

Keys only ever live in that file. Never commit it and never paste it into a terminal on a recording.

## 4. Provision

Copy the repo to the server the first time, or let the script clone it. Then:

```sh
ssh root@YOUR_IP
git clone https://github.com/kassem-kanjo/Hack-Western /tmp/darkfleet
DOMAIN=demo.example.com bash /tmp/darkfleet/deploy/provision.sh
```

Use `DOMAIN=demo.example.com` for a real domain. Omit `DOMAIN` to serve on port 80 by IP.

The script installs git, curl, Node 22, uv and Caddy. It clones or pulls the repo to `/opt/darkfleet`,
runs `uv sync`, builds the web app, installs the systemd units and starts both services.

If `.env` is missing it stops and prints:

```
copy .env to /opt/darkfleet/.env (scp .env root@IP:/opt/darkfleet/.env) then re-run
```

Copy the file as in step 3, then run `provision.sh` again. The script sets `.env` to mode 600.

Caddy gets a certificate automatically once DNS points at the server. Give it a minute.

## 5. Copy the snapshot

The API reads JSON from `/opt/darkfleet/data/snapshot`. Build it on your laptop with `make snapshot`,
then copy it up:

```sh
rsync -av data/snapshot/ root@YOUR_IP:/opt/darkfleet/data/snapshot/
```

The recorder writes live AIS files to `/opt/darkfleet/data/ais` on its own.

## 6. Check it

```sh
curl -s http://127.0.0.1:8000/api/health
curl -s https://demo.example.com/api/health
```

Both should return JSON with `"ok": true` and the snapshot path.

Logs:

```sh
journalctl -u darkfleet-api -f
journalctl -u darkfleet-recorder -f
journalctl -u caddy -f
```

Service control:

```sh
systemctl status darkfleet-api darkfleet-recorder caddy
systemctl restart darkfleet-api
```

## Updating

```sh
ssh root@YOUR_IP
DOMAIN=demo.example.com bash /opt/darkfleet/deploy/provision.sh
```

The script pulls, rebuilds and restarts. Your `.env` and `data/` stay in place.
