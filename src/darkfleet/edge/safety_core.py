"""Safety core: the high-priority, trusted half of the edge unit.

The safety core owns three things and nothing heavy:

* the AIS snapshot cache (a JSON list of {mmsi, lat, lon, t}, reloaded when the file changes),
* the alert state machine (OK / DEGRADED),
* the deadline clock (a frame older than `deadline_ms` on arrival is a deadline miss; silence
  for 3x the deadline means the detector is late and the unit reports DEGRADED).

It never blocks on the detector: every wait is a `select` with a short timeout, and a
detector message that is malformed drops that connection, not the core.

Rule (v1): a frame with a "boat" box raises ALERT "VESSEL NOT BROADCASTING" unless some cached
AIS vessel lies within `match_km` of the unit's own position. The camera bearing is not used yet.

Dependencies: standard library and `protocol` only (no pyproj, no numpy), so the module runs
on QNX with the same Python.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import signal
import socket
import stat
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import protocol

STATE_OK = "OK"
STATE_DEGRADED = "DEGRADED"
ALERT_KIND = "VESSEL NOT BROADCASTING"

_EARTH_RADIUS_KM = 6371.0088
_KM_PER_DEG = 110.0  # Deliberately a little small: the lat/lon pre-filter must never reject a true neighbour.
_TICK_S = 0.05
_SOCKET_PATH_MAX = 100  # AF_UNIX paths are limited to ~104 bytes on macOS.


@dataclass(frozen=True)
class CoreConfig:
    socket_path: str
    status_path: str
    alerts_path: str
    unit_lat: float
    unit_lon: float
    ais_path: str | None = None
    match_km: float = 2.0
    deadline_s: float = 0.2
    silence_factor: float = 3.0
    alert_cooldown_s: float = 5.0
    status_every_s: float = 0.1
    fifo_priority: int = 10
    nice_delta: int = -5
    set_priority: bool = True

    @property
    def silence_s(self) -> float:
        return self.silence_factor * self.deadline_s


@dataclass(frozen=True)
class AisVessel:
    mmsi: str
    lat: float
    lon: float
    t: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Good to well under 0.5 % at the 2 km scale we use."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def load_ais_snapshot(path: str | None) -> list[AisVessel]:
    """Read the AIS cache. A missing file means an empty cache (every boat then alerts).

    Raises ValueError on a file that exists but is not a JSON list; the caller keeps the old cache.
    Individual bad records are skipped.
    """
    if path is None or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("AIS snapshot must be a JSON list")
    vessels: list[AisVessel] = []
    for rec in raw:
        try:
            lat = float(rec["lat"])
            lon = float(rec["lon"])
            t = float(rec["t"])
            mmsi = str(rec["mmsi"])
        except (KeyError, TypeError, ValueError):
            continue
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            vessels.append(AisVessel(mmsi=mmsi, lat=lat, lon=lon, t=t))
    return vessels


def raise_priority(fifo_priority: int, nice_delta: int) -> str:
    """Try SCHED_FIFO, then a negative nice value, then nothing. Returns what was applied.

    macOS has no SCHED_FIFO and refuses negative nice for non-root users, so on the laptop
    this usually returns "default:0". On the Pi (root in the service) it returns "fifo:N".
    """
    fifo = getattr(os, "SCHED_FIFO", None)
    if fifo is not None:
        try:
            os.sched_setscheduler(0, fifo, os.sched_param(fifo_priority))
            return f"fifo:{fifo_priority}"
        except (OSError, AttributeError):
            pass
    try:
        new_nice = os.nice(nice_delta)
        return f"nice:{new_nice}"
    except OSError:
        return "default:0"


class SafetyCore:
    """Pure decision logic. No sockets, no files: `run` does the I/O around it."""

    def __init__(self, config: CoreConfig, vessels: list[AisVessel] | None = None) -> None:
        self.config = config
        self.vessels: list[AisVessel] = list(vessels or [])
        self.state = STATE_OK
        self.state_reason = "started"
        self.state_since_wall = time.time()
        self.detector_connected = False
        self.frames_total = 0
        self.deadline_misses = 0
        self.protocol_errors = 0
        self.alerts_total = 0
        self.alerts_suppressed = 0
        self.last_alert: dict[str, Any] | None = None
        self.last_frame_age_ms: float | None = None
        self.last_msg_wall: float | None = None  # wall clock of the last message, for the supervisor
        self.last_frame_wall: float | None = None  # wall clock of the last DETECTIONS
        self.last_inference_ms: float | None = None
        self.transitions: list[dict[str, Any]] = []
        self._last_msg_mono: float | None = None
        self._last_alert_mono: float | None = None
        # Starting in OK with no message yet: the silence clock starts at the first tick.
        self._started_mono: float | None = None

    # --- state machine -----------------------------------------------------

    def start(self, now: float) -> None:
        self._started_mono = now
        self._last_msg_mono = now

    def on_connect(self, now: float) -> None:
        self.detector_connected = True
        self._last_msg_mono = now

    def on_disconnect(self, now: float) -> None:
        self.detector_connected = False
        self._set_state(STATE_DEGRADED, "detector link lost", now)

    def tick(self, now: float) -> None:
        """Called every loop turn. Silence longer than 3x the deadline means DEGRADED."""
        if self._last_msg_mono is None:
            return
        if now - self._last_msg_mono > self.config.silence_s:
            silent_ms = round((now - self._last_msg_mono) * 1000)
            self._set_state(STATE_DEGRADED, f"no detector message for {silent_ms} ms", now)

    def on_message(self, msg: dict[str, Any], now: float) -> list[dict[str, Any]]:
        """Handle one validated message. Returns the alert records it raised (may be empty)."""
        self._last_msg_mono = now
        self.last_msg_wall = time.time()
        if self.state != STATE_OK:
            self._set_state(STATE_OK, "detector messages resumed", now)
        if msg["type"] != protocol.DETECTIONS:
            return []  # HEARTBEAT is liveness only; ALERT from a peer is not ours to act on.

        self.frames_total += 1
        self.last_frame_wall = self.last_msg_wall
        self.last_frame_age_ms = (now - msg["t_capture"]) * 1000.0
        self.last_inference_ms = (msg["t_done"] - msg["t_capture"]) * 1000.0
        if now - msg["t_capture"] > self.config.deadline_s:
            self.deadline_misses += 1
        return self._evaluate(msg, now)

    def note_protocol_error(self) -> None:
        self.protocol_errors += 1

    # --- rules -------------------------------------------------------------

    def nearest_vessel_km(self, radius_km: float) -> float | None:
        """Distance from the unit to the nearest cached vessel within `radius_km`, else None.

        A cheap lat/lon box pre-filter runs first; the haversine is only computed for the few
        vessels inside the box.
        """
        cfg = self.config
        dlat = radius_km / _KM_PER_DEG * 1.05
        cos_lat = max(math.cos(math.radians(cfg.unit_lat)), 0.01)
        dlon = radius_km / (_KM_PER_DEG * cos_lat) * 1.05
        best: float | None = None
        for v in self.vessels:
            if abs(v.lat - cfg.unit_lat) > dlat or abs(v.lon - cfg.unit_lon) > dlon:
                continue
            d = haversine_km(cfg.unit_lat, cfg.unit_lon, v.lat, v.lon)
            if d <= radius_km and (best is None or d < best):
                best = d
        return best

    def _evaluate(self, msg: dict[str, Any], now: float) -> list[dict[str, Any]]:
        boats = [b for b in msg["boxes"] if b["cls"] == "boat"]
        if not boats:
            return []
        if self.nearest_vessel_km(self.config.match_km) is not None:
            return []  # A vessel is broadcasting nearby: the boat is accounted for.
        if self._last_alert_mono is not None and now - self._last_alert_mono < self.config.alert_cooldown_s:
            self.alerts_suppressed += 1
            return []
        best = max(boats, key=lambda b: b["score"])
        # Report how far the nearest broadcasting vessel is (None if none within 10x match_km).
        nearest = self.nearest_vessel_km(10.0 * self.config.match_km)
        alert = protocol.make_alert(ALERT_KIND, msg["frame_id"], best["score"], best)
        record = {
            **alert,
            "t_wall": time.time(),
            "unit": {"lat": self.config.unit_lat, "lon": self.config.unit_lon},
            "nearest_ais_km": nearest,
            "ais_records": len(self.vessels),
        }
        self.alerts_total += 1
        self.last_alert = record
        self._last_alert_mono = now
        return [record]

    # --- bookkeeping -------------------------------------------------------

    def _set_state(self, state: str, reason: str, now: float) -> None:
        if state == self.state:
            return
        self.state = state
        self.state_reason = reason
        self.state_since_wall = time.time()
        self.transitions = (
            self.transitions + [{"t_wall": self.state_since_wall, "state": state, "reason": reason}]
        )[-20:]

    def status(self, *, priority: str, ais_error: str | None, now_wall: float) -> dict[str, Any]:
        cfg = self.config
        return {
            "component": "safety_core",
            "pid": os.getpid(),
            "t_wall": now_wall,
            "state": self.state,
            "state_reason": self.state_reason,
            "state_since_wall": self.state_since_wall,
            "priority": priority,
            "detector_connected": self.detector_connected,
            "frames_total": self.frames_total,
            "last_msg_wall": self.last_msg_wall,
            "last_frame_wall": self.last_frame_wall,
            "deadline_ms": round(cfg.deadline_s * 1000),
            "silence_ms": round(cfg.silence_s * 1000),
            "deadline_misses": self.deadline_misses,
            "protocol_errors": self.protocol_errors,
            "last_frame_age_ms": _round_or_none(self.last_frame_age_ms),
            "last_inference_ms": _round_or_none(self.last_inference_ms),
            "alerts_total": self.alerts_total,
            "alerts_suppressed": self.alerts_suppressed,
            "last_alert": self.last_alert,
            "ais_records": len(self.vessels),
            "ais_error": ais_error,
            "transitions": self.transitions,
        }


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class _AlertLog:
    """Append-only JSONL. Each record is fsynced: an alert must survive a power cut."""

    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())


def _write_json_atomic(path: str, obj: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True)
    os.replace(tmp, path)  # atomic on POSIX: readers see the old or the new file, never half of one


def _listen(path: str) -> socket.socket:
    if len(path.encode("utf-8")) > _SOCKET_PATH_MAX:
        raise ValueError(f"socket path too long for AF_UNIX: {path}")
    if os.path.exists(path):
        # Only remove a stale socket, never a regular file someone put there by mistake.
        if not stat.S_ISSOCK(os.stat(path).st_mode):
            raise FileExistsError(f"{path} exists and is not a socket")
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    srv.setblocking(False)
    return srv


class _AisCache:
    """Reloads the AIS snapshot when its mtime changes. A bad file keeps the previous cache."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self._mtime: float | None = None
        self.vessels: list[AisVessel] = []
        self.error: str | None = None

    def refresh(self) -> bool:
        if self.path is None:
            return False
        try:
            mtime = os.stat(self.path).st_mtime if os.path.exists(self.path) else None
        except OSError as exc:
            self.error = str(exc)
            return False
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        try:
            self.vessels = load_ais_snapshot(self.path)
            self.error = None
        except (OSError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return False
        return True


def run(
    config: CoreConfig,
    stop: threading.Event | None = None,
    duration_s: float | None = None,
    on_ready: Callable[[SafetyCore], None] | None = None,
) -> SafetyCore:
    """Serve the detector socket until `stop` is set or `duration_s` passes. Returns the core."""
    priority = (
        raise_priority(config.fifo_priority, config.nice_delta) if config.set_priority else "not requested"
    )
    print(f"safety core priority: {priority}", file=sys.stderr, flush=True)

    ais = _AisCache(config.ais_path)
    ais.refresh()
    core = SafetyCore(config, ais.vessels)
    alerts = _AlertLog(config.alerts_path)
    listener = _listen(config.socket_path)
    client: socket.socket | None = None
    decoder = protocol.MessageDecoder()

    start = time.monotonic()
    core.start(start)
    last_status = 0.0
    if on_ready is not None:
        on_ready(core)
    try:
        while stop is None or not stop.is_set():
            now = time.monotonic()
            if duration_s is not None and now - start >= duration_s:
                break
            rlist = [listener] if client is None else [listener, client]
            readable, _, _ = select.select(rlist, [], [], _TICK_S)
            now = time.monotonic()

            if listener in readable:
                new_client = _accept(listener, client, core, now)
                if new_client is not client:
                    client = new_client
                    decoder = protocol.MessageDecoder()  # a new link starts with an empty buffer
            if client is not None and client in readable:
                client, closed = _read(client, decoder, core, alerts, now)
                if closed:
                    core.on_disconnect(now)

            core.tick(now)
            if now - last_status >= config.status_every_s:
                if ais.refresh():
                    core.vessels = ais.vessels
                last_status = now
                _write_json_atomic(
                    config.status_path,
                    core.status(priority=priority, ais_error=ais.error, now_wall=time.time()),
                )
    finally:
        if client is not None:
            client.close()
        listener.close()
        try:
            os.unlink(config.socket_path)
        except OSError:
            pass
        _write_json_atomic(
            config.status_path,
            core.status(priority=priority, ais_error=ais.error, now_wall=time.time()),
        )
    return core


def _accept(
    listener: socket.socket,
    client: socket.socket | None,
    core: SafetyCore,
    now: float,
) -> socket.socket | None:
    try:
        conn, _ = listener.accept()
    except BlockingIOError:
        return client
    if client is not None:
        # A restarted detector replaces the old link; the old one is dead or stale.
        client.close()
    conn.setblocking(False)
    core.on_connect(now)
    return conn


def _read(
    client: socket.socket,
    decoder: protocol.MessageDecoder,
    core: SafetyCore,
    alerts: _AlertLog,
    now: float,
) -> tuple[socket.socket | None, bool]:
    """Read what is available. Returns (client or None, closed?)."""
    try:
        data = client.recv(65536)
    except (BlockingIOError, InterruptedError):
        return client, False
    except OSError:
        data = b""
    if not data:
        client.close()
        return None, True
    try:
        messages = decoder.feed(data)
    except protocol.ProtocolError:
        # Untrusted peer sent garbage: drop it. The decoder state is unusable, so reset the link.
        core.note_protocol_error()
        client.close()
        return None, True
    for msg in messages:
        for record in core.on_message(msg, now):
            alerts.append(record)
    return client, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edge safety core (high priority).")
    parser.add_argument("--socket", required=True, help="Unix socket the detector connects to")
    parser.add_argument("--status", required=True, help="safety status JSON, rewritten every 0.5 s")
    parser.add_argument("--alerts", required=True, help="append-only alerts JSONL")
    parser.add_argument("--ais", default=None, help="AIS snapshot JSON: list of {mmsi, lat, lon, t}")
    parser.add_argument("--lat", type=float, required=True, help="unit latitude")
    parser.add_argument("--lon", type=float, required=True, help="unit longitude")
    parser.add_argument("--match-km", type=float, default=2.0)
    parser.add_argument("--deadline-ms", type=float, default=200.0)
    parser.add_argument("--status-every-s", type=float, default=0.1)
    parser.add_argument("--cooldown-s", type=float, default=5.0)
    parser.add_argument("--no-priority", action="store_true", help="do not change scheduling")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds (tests, demos)")
    args = parser.parse_args(argv)

    config = CoreConfig(
        socket_path=args.socket,
        status_path=args.status,
        alerts_path=args.alerts,
        unit_lat=args.lat,
        unit_lon=args.lon,
        ais_path=args.ais,
        match_km=args.match_km,
        deadline_s=args.deadline_ms / 1000.0,
        alert_cooldown_s=args.cooldown_s,
        status_every_s=args.status_every_s,
        set_priority=not args.no_priority,
    )
    stop = threading.Event()

    def _on_term(_signum: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _on_term)
    try:
        run(config, stop=stop, duration_s=args.duration)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
