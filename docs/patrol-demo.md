# Patrol unit demo: AI lookout (QNX 8.0 Raspberry Pi 5, USB webcam) - issue #217

Bridge collision tools rely on AIS for identity, course and closest approach; a ship with AIS off has none of that until a person works it out. Camera lookout products exist for this reason; ours keeps its alert deadline when the AI is overloaded.

**Start (Pi):** `bash scripts/edge_deploy.sh qnxuser@<pi-ip> --qnxcam 5:flip --lat 54.45 --lon 10.25 --patrol-name "Kiel Bight" --ais data/edge/patrol_ais.json --act-led` (`EDGE_STATUS_TOKEN` from `.env` in the environment; AIS file from `scripts/make_patrol_ais.py --radius-km 10`). **Laptop:** `EDGE_HOST=<pi-ip> EDGE_DEMO_CONTROLS=1 DARKFLEET_WEB_DIST=web/dist uv run uvicorn darkfleet.api.app:app --port 8015`, open `#/patrol`. **Stop:** `ssh qnxuser@<pi-ip> 'kill $(cat darkfleet/supervisor.pid) $(cat darkfleet/status_http.pid)'`.

**What it does.** A vessel box from the AI with no AIS ship within 2 km: red `VESSEL NOT ON AIS - alert bridge`, with `closing`/`opening` from the box growth, a time at this rate and a range (both rough estimates: assumes a 100 m ship; no closest-approach claim). Hard deadline: frame timestamp to alarm decision 200 ms, every alarm's latency measured, shown as `Alarms on time N/N` and `p99 / 200 ms`. No frame for 500 ms (AI dead or stalled, camera stale, still loading) = amber `LOOKOUT BLIND - manual watch`, raised by the core's own 20 Hz tick (budget 100 ms), cleared when frames return. Otherwise `WATCHING`.
**Prop:** a ship photo (`data/demo_ship_min.jpg`, Wikimedia Commons "The cargo ship Min on sea trials") held 30-60 cm from the webcam. The patrol point has no AIS ship within 2 km, so any ship seen is not on AIS.
**Click:** Overload the Pi (10 s): 4 busy loops at the default priority; alarms stay on time. Kill the AI detector: BLIND flashes up, the warm standby takes over (~150 ms), the button shows the measured recovery.

**QNX findings on this image (8.0.0, RPi5), checked on the device**
- Adaptive partitioning: absent. No `aps` binary, no `sys/sched_aps.h`, no partition column in `pidin`. Used instead: fixed priorities, safety core SCHED_FIFO 30 > supervisor FIFO 25 > camera helper RR 15 > AI RR 12 > all else 8. (The AI at RR 20 starved the camera service: stream stopped after 3 frames.)
- HAM: absent. QNX 8's replacement `slm`/`slmctl` is present; a private `slm` instance restarted a killed component (logged "terminated by uncaught signal 15", component back in `active`). Not wired in: a cold restart reloads the model, our warm standby recovers in ~150 ms.
- `wdtkick` (watchdog) is in the boot image; no watchdog hardware was configured, not used.
- GPIO: `/dev/gpio/<n>` (rpi_gpio) for the 40-pin header. The onboard ACT LED is AON GPIO 9 (`/2712_STAT_LED`): `sudo gpio-aon-bcm set 9 op dl|dh` worked and read back. `--act-led`: solid = vessel not on AIS, fast blink = BLIND; polarity (low = on) assumed from the Pi 5 device tree, not seen by eye: if it is inverted, run the indicator with `--active-high`.
- HDMI: Screen runs, GTK4 (`GDK_BACKEND=qnx`, `python3-gobject3` installed) opens a window without error, but nothing from it showed in `screenshot`. Dropped: use the app.

**Measured on the Pi (21 Nov 2026), `scripts/qnx_ab_run.sh`: same ship photo, 4 busy loops for 10 s**
| | no guard | guard |
|---|---|---|
| alarms on time (<= 200 ms) | 10/27 | 63/64 |
| worst alarm latency | 454 ms | 201 ms |
| worst safety-loop lateness | 24 ms | 1.8 ms |
| BLIND episodes | 16 | 0 |
Idle: inference 105 ms, 8-9 fps, loop median 0.7 ms. Earlier runs of the guard arm: 63/63 (189 ms), 62/63 (209 ms). One alarm in the guarded run missed by 1.2 ms: the guard holds the deadline to within a frame, not absolutely. Limits: the AI only knows the COCO class "boat"; the closing estimate needs a steady box.
