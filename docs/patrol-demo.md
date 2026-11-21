# Patrol unit demo (QNX Raspberry Pi 5, USB webcam)

**Start (Pi):** `set -a; . ./token.env; set +a; bash scripts/edge_deploy.sh qnxuser@<pi-ip> --qnxcam 5:flip --lat 54.45 --lon 10.25 --patrol-name "Kiel Bight" --ais data/edge/patrol_ais.json` (AIS: `scripts/make_patrol_ais.py --lat 54.45 --lon 10.25 --radius-km 10`; `EDGE_STATUS_TOKEN` in `token.env`).
**Start (laptop):** `EDGE_HOST=<pi-ip> EDGE_STATUS_TOKEN=... EDGE_DEMO_CONTROLS=1 DARKFLEET_WEB_DIST=web/dist uv run uvicorn darkfleet.api.app:app --port 8015`, open `#/patrol`.
**Stop:** `ssh qnxuser@<pi-ip> 'kill $(cat darkfleet/supervisor.pid) $(cat darkfleet/status_http.pid)'`. Redeploying is safe: it waits for the old unit to release the camera.

**The prop:** hold a ship photo (phone or print) 30-60 cm in front of the webcam, ship filling most of the frame, sideways on, ceiling out of view. Tested photos (Wikimedia Commons, "The cargo ship Min on sea trials", "Launch of the cargo ship Troutpool"): copies in `data/demo_ship_min.jpg`, `data/demo_ship_troutpool.jpg`. Expected: green `boat 0.8` box, verdict `ALERT: Vessel seen, not broadcasting`.
The patrol point is in Kiel Bight with 0 AIS ships inside the 2 km match radius (26 within 10 km), so any ship in view is a ship nobody reports. Move the photo away: `IDLE: No vessel in view`.

**What to click, what the judge sees**
1. Camera, AI detector (untrusted), safety core: three steps, then the one-line verdict and the patrol position.
2. Stress the AI (10 s): all four cores busy at the AI's priority. AI time goes 107 ms to about 340 ms and the frame rate 5 to 2, while the safety loop stays at the same 20 Hz.
3. Kill the AI detector: the safety core says `AI DOWN: sensor blind, manual watch`, keeps the AIS cross-check and its 20 Hz loop, and a warm standby (model already loaded) takes over. The button then shows the measured recovery.
4. Camera stops delivering frames (tested by starving the source, not by unplugging): `Camera offline` replaces the frame. Stop the Pi's status server: `Patrol unit offline`.

**Measured on the Pi (21 Nov 2026, OpenCV DNN, YOLOX-nano at 320 px, camera 640x480)**
- Inference 105-110 ms idle, 5 frames/s, 0 deadline misses of 200 ms. Under stress 335-373 ms, 2 frames/s, 21 misses in 10 s.
- Recovery after Kill, kill to first new detection: 130, 142, 150, 151, 157, 157 ms (six kills, supervisor clock). Without the standby the cold restart was 2.8 s.
- Safety loop at 20 Hz: median late 0.6 ms, p99 0.9 ms, max 1-2 ms; under stress max 4.6 ms, no tick more than 5 ms late.

Limits: the detector only knows the COCO class "boat". The safety core's alert needs that box, so a ship the AI misses is missed. Kill and Stress need `EDGE_DEMO_CONTROLS=1`, and with a remote Pi also `EDGE_STATUS_TOKEN`.
