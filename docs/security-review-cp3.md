# Security review — CP3 first pass

Scope: `src/darkfleet/api/*.py`, `src/darkfleet/edge/*.py`, `scripts/edge_deploy.sh`,
`deploy/*` (provision.sh, units, Caddyfile), `scripts/sunday.sh`, `scripts/record_ais.py`,
`web/src/**`, `.gitignore` + `git ls-files`. Read-only review: no product code was changed.

Method: code reading plus four live probes against the real app (TestClient, a local uvicorn on
:8123, and two HTTP clients). Evidence is quoted per finding. Threat model assumed by the review:
the demo VPS is public (Caddy → 127.0.0.1:8000), the edge unit sits on a shared/LAN network, and
AIS content (vessel names, positions) is attacker-controlled by design.

## Findings

| id | Severity | file:line | What an attacker could do | Concrete fix |
|----|----------|-----------|---------------------------|--------------|
| H-1 | High | `src/darkfleet/api/live.py:202-233` (esp. `:233` `time.sleep`) | Open N `/api/live/<REGION>/stream` requests and never disconnect. Every stream is a **sync generator** (`time.sleep(5)` inside `event_stream`), so each one permanently occupies an anyio worker thread (default limit 40). With 40-50 open streams the entire API stalls — including `/api/health`, which the demo gate polls. **Measured** on local uvicorn: 45 open streams → `/api/health` 8.14 s, `/api/cases` 10.04 s. Unauthenticated, one shell loop, breaks the demo. | Make `event_stream` `async` and `await asyncio.sleep(...)`; or guard the route with a process-wide `asyncio.Semaphore(8)` and return 429/503 beyond it; add uvicorn `--limit-concurrency 64` as a backstop. Don't run a blocking `sleep` in a sync StreamingResponse iterator. |
| M-1 | Medium (High if the demo box sets `EDGE_DEMO_CONTROLS=1`) | `src/darkfleet/api/edge.py:147-163` (`:150` only check) | `POST /api/edge/kill` is authorised by one env var and nothing else: no token, no `Origin`/`Sec-Fetch-Site` check, no CSRF token. A plain HTML form on any site the operator visits — or any internet client via Caddy — gets **202 and writes `control/kill_detector`**. Verified: `POST /api/edge/kill` with `content-type: application/x-www-form-urlencoded` and `Origin: https://evil.example` → `202 {"ok":true}` and the kill file appeared. (Without the env var it is correctly 403; with `EDGE_HOST` set it is 409.) | Require a shared secret header (e.g. `X-Demo-Token` compared to a `.env` value with `secrets.compare_digest`) **and** reject requests whose `Origin`/`Referer` is present and not same-origin. Alternatively gate `/kill` on the request coming from `127.0.0.1` and keep it off the public Caddy route. |
| M-2 | Medium | `src/darkfleet/api/edge.py:68-89` (`:86` `response.content`) | `/api/edge/frame.jpg` and `/api/edge/status` are unauthenticated relays to `EDGE_HOST` with **no body size cap** and no total time limit: `httpx` `timeout=1.0` is per-operation (connect/read/write), so a peer sending 1 byte/0.9 s streams without limit into `response.content` → API memory exhaustion. Any internet client can also use the API as a GET-only probe/reader into the Pi's LAN (`http://<EDGE_HOST>:7071/...`) and amplify load onto the Pi once per request (no caching). | Stream the response (`client.stream(...)`) and abort after a hard deadline plus a byte cap (≤3 MB JPEG, ≤64 KB JSON); validate `Content-Type`; cache `/status` for ~1 s. |
| M-3 | Medium | `src/darkfleet/api/live.py:281-322` (`:310` `handle.read()`; `:297-304` resync) | `_GzipTail.poll` reads the **whole remaining file** into RAM every tick, and after any deflate error re-reads from byte 0 of the hour file. One object per SSE connection, so N clients = N copies of the hour file (~tens of MB decompressed for a busy region). Combined with H-1 this is a cheap OOM on the 2 GB demo box. | Read a bounded chunk per tick (`handle.read(min(size - offset, 4 << 20))`), advance the offset past anything not returned, and cap the resync re-read to the last N MB. |
| M-4 | Medium | `src/darkfleet/edge/sources.py:139,150-200` (`:139` `host="0.0.0.0"`, `:185` 1 s, `:194`) | `NetSource` listens on **all interfaces** with no authentication, accepts **one** client, and `_recv_exact` only enforces a per-chunk 1 s timeout against a length of up to 8 MiB. Anyone on the LAN connects, sends a 4-byte header and then 1 byte every 0.9 s: the detector never gets another frame, never accepts the real bridge, the safety core goes DEGRADED and the supervisor kill/restart loop runs. | Default `host="127.0.0.1"` (the Pi's `--source net:7070` must then pass an explicit `--host`); enforce a total per-frame deadline across all chunks and drop the connection when exceeded; optionally require a shared nonce in the header. |
| M-5 | Medium | `src/darkfleet/edge/sources.py:274-283` (`:280`) | Frames from `NetSource` are attacker bytes; `cv2.imdecode` allocates `w*h*3` from the file header alone. An 8 MiB JPEG can declare 30000×30000 (≈2.7 GB RGB) → OOM/crash loop of the detector on the Pi. The 8 MiB byte cap does not bound the decoded size. | Parse the SOF dimensions from the JPEG header before decoding and reject anything over ~1920×1080; lower `MAX_FRAME_BYTES` to 2 MiB; decode with `IMREAD_REDUCED_COLOR_2`. |
| M-6 | Medium | `src/darkfleet/edge/status_http.py:87,95` (`:88`/`:96` default `0.0.0.0`) | The Pi serves `edge_status.json` **and the live annotated camera frame** on 0.0.0.0:7071 with no auth and no CORS. Anyone on the LAN (conference wifi) can poll and save the video feed of whatever the unit is pointed at, and read the supervisor's status. `scripts/edge_deploy.sh:137-140` starts it exactly this way. | Default `--host 127.0.0.1` in both `main()` and `make_server()`; reach it over `ssh -L 7071:127.0.0.1:7071`, or require a `?token=` shared secret when a non-loopback host is given. |
| L-1 | Low | `scripts/rerank_cases.py:3,299`, `scripts/validate.py:833`, `scripts/validate_bb.py:134`, `scripts/validate_holdout.py:67` | Tracked files hard-code `/Users/kassemkanjo/Desktop/Hack Western` and default `--env` to that path. Publishes the operator's local username and layout, and makes the scripts read a file outside the repo by default (so a caller who forgets `--env` silently loads someone's `.env`). | Remove the defaults: make `--root`/`--env` required (or default to the repo root / `.env`) and drop the absolute path from the module docstring. |
| L-2 | Low | `web/src/components/VesselCard.ts:174,187-198` | The one data-driven `href` in the UI: `watch.entry.url` from the snapshot JSON is passed straight to `setAttribute("href", ...)`. A snapshot containing `javascript:...` (hand-edited JSON, or a future ingest bug) executes in the console origin when the "watchlist link" is clicked. Everything else in the app is text nodes, so this is the only such sink. | Allow-list the scheme before rendering: only `new URL(url)` with `protocol === "https:"` (or `http:`), otherwise render the URL as plain text. |
| L-3 | Low | `deploy/Caddyfile:4-20`; `web/index.html` | No CSP, `X-Content-Type-Options`, `Referrer-Policy` or `frame-ancestors` anywhere. The public site can be framed by any page, so the Patrol "Kill the AI detector" button can be clickjacked if controls are on; any future markup mistake is immediately script-executable. | Add to Caddy: `header { Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'" X-Content-Type-Options nosniff Referrer-Policy no-referrer }`. |
| L-4 | Low | `src/darkfleet/api/app.py:142-150` | `/api/health` returns `"source": "<absolute snapshot path>"` to any client — confirmed `/var/folders/.../snapshot`. Discloses host layout, usernames and the deploy directory (`/opt/darkfleet/data/snapshot`) for free. | Return the basename, or `{"ok": true, "cases": n}` plus the snapshot `generated_at`, and log the full path server-side only. |
| L-5 | Low | `src/darkfleet/api/app.py:42,168-187` | FastAPI docs are enabled in production: `/docs` and `/openapi.json` return 200 (verified) and enumerate every route including `POST /api/edge/kill` and its schema. Free reconnaissance for M-1. | `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` unless `DARKFLEET_DEV=1`; the browser never needs them. |
| L-6 | Low | `src/darkfleet/edge/safety_core.py:318-330,437-445` | The AF_UNIX socket is created with the default umask and `_accept` closes the existing client whenever anyone new connects. Any other local account on the Pi can hijack the trusted detector link, inject forged `DETECTIONS` (fake boats → fake "VESSEL NOT BROADCASTING" alerts) and silence the real detector. | `os.chmod(socket_path, 0o600)` after `bind` (and create the state dir 0700); on QNX/Linux additionally check peer credentials (`SO_PEERCRED`) if more than one account exists. |
| L-7 | Low | `scripts/sunday.sh:48-54` | `.env` is sourced with `set -a`, so `DATABASE_URL` (with the Tiger password) and the GFW/AIS keys are exported to **every** child of the run, including `npm`/`node media/record.mjs`/`npx` tooling. One compromised npm lifecycle script reads the DB password. | Source `.env` without `set -a` and explicitly `export` only the keys each step needs (or pass them via `env X=...` on the specific command). |
| I-1 | Info | `deploy/provision.sh:46,52` | Both NodeSource and uv are installed by piping a downloaded script into a shell as root (`curl … bash -` at `:46`, `curl … sh` at `:52`): a compromise of those endpoints yields root before any repo code runs. (Caddy is done properly: signed apt repo + dearmor.) | Pin the installers: use the NodeSource apt repo with a pinned key, and fetch a pinned `uv` release with a checksum; or install both from a prebuilt image. |
| I-2 | Info | `deploy/darkfleet-api.service:20-21`, `deploy/darkfleet-recorder.service:16-17` | Units set only `NoNewPrivileges` + `PrivateTmp`. A bug in the API or recorder gets the whole filesystem instead of just its data dirs. | Add `ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths=/opt/darkfleet/data`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `PrivateDevices=true`, `CapabilityBoundingSet=`. |
| I-3 | Info | `scripts/edge_deploy.sh:20` | `StrictHostKeyChecking=accept-new` trusts whatever answers first on the LAN: a spoofed Pi receives the model, the supervisor log stream and the deploy. No secrets are copied, so impact is limited to code/logs. | Print and pin the Pi's host key in the runbook (or use `StrictHostKeyChecking=yes` with a prepared `known_hosts`), and keep the deploy on a trusted network. |
| I-4 | Info | `docs/STATUS.md:37` | The tracked status doc records that the Tiger credentials were downloaded as plain text to `~/Downloads/tiger-cloud-hack-western-credentials.env` and copied into `.env`. No secret is in git, but the plaintext copy is still on disk and the DB password has been in a Downloads folder. | Rotate the Tiger password after the demo, delete the Downloads file, and drop the note from STATUS once done (it is a pointer to a credential, not a review item). |

## Checked and fine

- **Path traversal in static/SPA serving** (`app.py:162-187`): Starlette `StaticFiles` resolves
  each request with `realpath` + `commonpath` against the dist root, and the SPA fallback can only
  ever return `dist/index.html`. Six payloads against the real app — `/../secret.txt`,
  `/%2e%2e/secret.txt`, `/..%2fsecret.txt`, `/assets/../../secret.txt`, `/%2e%2e%2fsecret.txt`,
  `//etc/passwd` — all returned the index page (200), and a decoy `secret.txt` next to `dist/` was
  never served. (Cosmetic only: traversal attempts get 200 + index, not 404, so the SPA answers
  every unknown path.)
- **XSS via vessel names**: `grep -R "innerHTML\|outerHTML\|insertAdjacentHTML\|document.write"` over
  `web/src` returns zero hits. `components/dom.ts:14-37` builds text nodes (`createTextNode`), and
  `map/liveMap.ts:848` uses `textContent` for the vessel label, so an AIS name of
  `<img src=x onerror=alert(1)>` renders literally. Names are also never interpolated into
  `cssText` (only token colours are). `web/index.html` has no inline script and no CDN tags.
- **No third-party calls from the browser**: the basemap is committed GeoJSON referenced relatively
  (`basemap.ts:29-40`), the style has no glyphs/sprite/remote tiles, and the only absolute URLs in
  `web/src` are the GFW / OpenSanctions attribution links. Every data call is same-origin:
  `api.ts:173`, `components/hormuz.ts:31`, `present/present.ts:165`.
- **CORS** (`app.py:44-49`): origins are exactly `http://localhost:5173` and `http://127.0.0.1:5173`,
  methods `GET` only. A preflight from `https://evil.example` in the probe returned 400 with no
  `Access-Control-Allow-Origin`, so cross-origin **reads** are blocked (the write path is M-1).
- **Kill endpoint gating**: with `EDGE_DEMO_CONTROLS` unset, `POST /api/edge/kill` is 403 (verified);
  with `EDGE_HOST` set it is 409 (verified in `tests/test_api_edge.py:177-190`). The control file is
  written via `os.replace` of a `.tmp` sibling, so no partial write is possible.
- **Region → filename** (`live.py:33-34,63-80`): `^[A-Z_]+$` is enforced before any join.
  `/api/live/..%2f..%2fetc/summary` → 404, `/api/live/baltic/summary` → 400, and the AIS tail path
  (`live.py:229`) uses the same validated region. No write endpoint takes a path from the request.
- **Protocol length limits** (`edge/protocol.py:22,140-145,175-188,191-208`): a message is capped at
  1 MiB **before** allocation on both the encode and the decode path; `MessageDecoder` grows only up
  to `4 + declared_length`; a declared length over the cap raises `ProtocolError` and the safety
  core drops the peer (`safety_core.py:465-471`) instead of allocating. `boxes`/`box` fields are type
  checked and `bool` cannot pass as an int/number.
- **Supervisor control file** (`edge/supervisor.py:249-263`): the kill file is consumed
  (`os.unlink` + `proc.kill()`) with the same restart/backoff path as any crash, and no value from
  the file is read or executed — the file's *existence* is the whole signal.
- **Secrets not tracked**: `git ls-files` contains no `.env`, no model, and nothing under
  `data/sanctions/`, `data/cache/`, `data/snapshot/` or `data/edge/` (all covered by `.gitignore`).
  Grepping every tracked file for `sk-…`, JWTs, `postgres://user:pass@` and `*_API_KEY=`/`*_TOKEN=`
  assignments found nothing. `provision.sh:79-80` chmods `.env` to 600 and hands it to the service
  user only.
- **Secrets never logged**: `sunday.sh:49` logs only "loading .env (contents not echoed)", no
  `set -x`, and the `ERR` trap prints `$BASH_COMMAND` (the command line), not the environment.
  `record_ais.py:62` sends the AISStream key inside the first JSON message, never in the URL, and
  the reconnect line (`:91`) prints only `exc!r`. `GfwClient` errors are constructed without the
  token (`ingest/gfw.py:281-339`).
- **No command injection in `provision.sh`**: `DOMAIN` is validated against `^[A-Za-z0-9.-]+$`
  (`:24-27`) before the `sed` that writes the Caddyfile (`:112`), so no `/`, `&`, newline or quote
  can reach `sed`; the Caddy site block is the only interpolated value.
- **No command injection in `edge_deploy.sh`**: `TARGET` must contain `@` (`:57`), every use is
  double-quoted, `-*` arguments are rejected by the parser (`:40-44`), and `REMOTE_SOURCE` is one of
  two internal constants — no `$1` is ever pasted into the remote shell script.
- **SSRF surface**: `EDGE_HOST` comes from the process environment only (no request parameter
  reaches it) and the URL is hard-coded to `http://<host>:7071/{edge_status.json,latest.jpg}`;
  `httpx` defaults to `follow_redirects=False`, so a 302 from the Pi cannot pivot the request. The
  remaining risk is the response handling (M-2), not the URL construction.
- **Binding**: `darkfleet-api.service:17` runs uvicorn on `127.0.0.1:8000` behind Caddy; `make
  demo-check` and `sunday.sh:107-108` start their own server on the default loopback address. The
  only 0.0.0.0 listeners found are the edge ones (M-4, M-6).
- **`record_ais.py` file paths**: hour files are built from the fixed `REGIONS` table
  (`:24-35,39-41`) and the region list is validated against that table (`:106-108`); nothing from
  the upstream JSON (latitude/longitude included) reaches a filename.
- **`.gitignore` coverage**: `.env`, `.env.*` (with `!.env.example`), `data/sanctions/`,
  `data/snapshot/`, `data/edge/`, `data/cache/` and `*.onnx` are all ignored, matching what the
  review expects to be private.
