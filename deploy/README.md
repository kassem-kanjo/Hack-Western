# Deploy Dark Fleet on one VPS

One Ubuntu 24.04 box runs everything. Caddy listens on port 80/443. uvicorn serves the API and the
built web console from the same process. Three systemd services run on the box: the API, the AIS
recorder and the watch builder. systemd restarts them if they crash.

## 1. Create the server

In Vultr, deploy **Ubuntu 24.04**, **1 vCPU / 4 GB RAM**, in the region closest to you. Add your SSH
key. Note the public IP address. The watch build reads a day of recordings on every pass, so it
needs the 4 GB plan; 2 GB runs out of memory and the service gets killed.

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

## 4. Copy the recording

Before starting the services, copy the data the site needs to show something on its first load.
From the repo root on your laptop:

```sh
deploy/sync-data.sh root@YOUR_IP
```

It rsyncs `data/snapshot/` to `/opt/darkfleet/data/snapshot/`, `data/cache/ais/` to
`/opt/darkfleet/data/ais/` and `data/sanctions/` (if present) to `/opt/darkfleet/data/sanctions/`,
then hands `/opt/darkfleet/data` to the `darkfleet` user. It never copies `.env`, and it never
deletes anything already on the server, so it is safe to re-run as you record more data.

## 5. Provision

Copy the repo to the server the first time, or let the script clone it. Then:

```sh
ssh root@YOUR_IP
git clone https://github.com/kassem-kanjo/Hack-Western /tmp/darkfleet
DOMAIN=demo.example.com bash /tmp/darkfleet/deploy/provision.sh
```

Use `DOMAIN=demo.example.com` for a real domain. Omit `DOMAIN` to serve on port 80 by IP.

The script installs git, curl, Node 22, uv and Caddy. It clones or pulls the repo to `/opt/darkfleet`,
runs `uv sync`, builds the web app, installs the systemd units and starts all three services:
`darkfleet-api`, `darkfleet-recorder` (writes the gzipped AIS files) and `darkfleet-watch` (rebuilds
the alerts snapshot every 60 seconds from those files).

If `.env` is missing it stops and prints:

```
copy .env to /opt/darkfleet/.env (scp .env root@IP:/opt/darkfleet/.env) then re-run
```

Copy the file as in step 3, then run `provision.sh` again. The script sets `.env` to mode 600.

Caddy gets a certificate automatically once DNS points at the server. Give it a minute.

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
journalctl -u darkfleet-watch -f
journalctl -u caddy -f
```

Service control:

```sh
systemctl status darkfleet-api darkfleet-recorder darkfleet-watch caddy
systemctl restart darkfleet-api
```

The public site runs with `DARKFLEET_PUBLIC=1`, so `/docs` and the demo controls are off.

The QNX Pi on the Patrol screen cannot be reached from the cloud, so on the public site that screen
shows the last snapshot we uploaded rather than a live feed.

## Updating

```sh
ssh root@YOUR_IP
DOMAIN=demo.example.com bash /opt/darkfleet/deploy/provision.sh
```

The script pulls, rebuilds and restarts. Your `.env` and `data/` stay in place. To add fresh
recordings, run `deploy/sync-data.sh root@YOUR_IP` from your laptop.
