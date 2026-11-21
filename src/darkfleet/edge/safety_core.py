"""Safety core: the high-priority, trusted half of the edge unit.

The safety core owns three things and nothing heavy:

* the AIS snapshot cache (a JSON list of {mmsi, lat, lon, t}, reloaded when the file changes),
* the alert state machine (OK / DEGRADED),
* the deadline clock (a frame older than `deadline_ms` on arrival is a deadline miss; silence
  for 3x the deadline means the detector is late and the unit reports DEGRADED).

It never blocks on the detector: every wait is a `select` with a short timeout, and a
detector message that is malformed drops that connection, not the core.

AI lookout: a vessel box from the AI with no cached AIS vessel within `match_km` of the unit
raises "VESSEL NOT ON AIS - alert bridge" (alarm kind ``not_on_ais``). Severity is "critical" when
the box is growing across frames ("closing"), else "warning". Range and time-to-collision come
from the box width and its growth and are ESTIMATES (they assume a 100 m vessel and a 60 degree
camera). The alarm has a hard deadline: frame timestamp to decision <= `deadline_ms` (200), every
alarm's measured latency is kept and the status reports "alarms on time N/N".

Fail-safe, not fail-silent: if no frame has reached the core for 500 ms (AI dead or stalled, camera
stale, link lost, still loading) the core raises "LOOKOUT BLIND - manual watch" from its own 20 Hz
tick and keeps it until frames return. The camera bearing is not used.

The core also owns the decision file: after every DETECTIONS message it builds
``<state-dir>/latest_decision.json`` (AI boxes, patrol position, nearby AIS, verdict) and
``run`` replaces it atomically. Its 20 Hz housekeeping loop uses absolute ``time.monotonic``
deadlines and reports its own lateness as ``safety_tick`` in the status file.

Dependencies: standard library and `protocol` only (no pyproj, no numpy), so the module runs
on QNX with the same Python.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import select
import signal
import socket
import stat
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import protocol, realtime
from .sightings import SightingsLog

STATE_OK = "OK"
STATE_DEGRADED = "DEGRADED"
ALERT_KIND = "not_on_ais"
BLIND_KIND = "lookout_blind"
VESSEL_LABELS = frozenset({"boat", "ship"})
DECISION_NAME = "latest_decision.json"
NOT_ON_AIS_TEXT = "VESSEL NOT ON AIS \u2014 alert bridge"
BLIND_TEXT = "LOOKOUT BLIND \u2014 manual watch"
BLIND_AFTER_S = 0.5  # no frame for this long: the lookout is blind
BLIND_DEADLINE_MS = 100.0  # the blind alarm must be up within this long after the 500 ms
ASSUMED_VESSEL_M = 100.0  # range estimate only: how long we pretend the vessel is
CAMERA_HFOV_DEG = 60.0  # Logitech C270, roughly
CLOSING_RATIO = 1.12  # box width grew by 12 % over the last 0.5-2 s: closing
OPENING_RATIO = 0.89
_TRACK_WINDOW_S = 2.0
VERDICT_ALERT = "alert"
VERDICT_OK = "ok"
VERDICT_IDLE = "idle"

_EARTH_RADIUS_KM = 6371.0088
_KM_PER_DEG = 110.0  # Deliberately a little small: the lat/lon pre-filter must never reject a true neighbour.
_SOCKET_PATH_MAX = 100  # AF_UNIX paths are limited to ~104 bytes on macOS.
# The housekeeping loop is 20 Hz. A tick more than this late counts as a miss for
# `late_count_60s`; p99/max report the raw lateness either way.
TICK_HZ = 20.0
LATE_TOLERANCE_MS = 5.0
_SELECT_MAX_S = 0.5  # wake at least twice a second even if a longer tick period is configured
# Statistical windows. One minute matches the console's "Deadline misses (60 s)";
# frames_per_s is a short window so a stalled camera shows up almost immediately.
_STATS_WINDOW_S = 60.0
_FRAME_RATE_WINDOW_S = 1.0


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
    fifo_priority: int = 30  # real-time class on QNX; refused on macOS and logged once
    set_priority: bool = True
    # Decision contract: where the frame-by-frame verdict is written, what the unit is called
    # and which boxes count as a vessel.
    decision_path: str | None = None
    patrol_name: str = "patrol"
    source: str = "unknown"
    score_min: float = 0.3
    tick_hz: float = TICK_HZ
    ais_max_age_s: float = 900.0
    sightings_path: str | None = None  # hash-chained log of not_on_ais alerts
    frame_width_px: int = 640  # width the box coordinates refer to (range estimate)

    @property
    def silence_s(self) -> float:
        return self.silence_factor * self.deadline_s


@dataclass(frozen=True)
class AisVessel:
    mmsi: str
    lat: float
    lon: float
    t: float
    name: str = ""


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
            name = rec.get("name")
            vessels.append(AisVessel(mmsi=mmsi, lat=lat, lon=lon, t=t, name=str(name) if name else ""))
    return vessels


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
        self.detector_waiting = False  # the last heartbeat said "waiting for frames"
        self.detector_sched: dict[str, Any] | None = None  # the detector's own scheduling class
        self.transitions: list[dict[str, Any]] = []
        self.last_frame_id = 0
        # Alarm path: what the unit is raising now, and how fast it raised it.
        self.alarm_level = "none"  # none | warning | critical | blind
        self.alarm_text = ""
        self.blind = False
        self._last_frame_mono: float | None = None  # when the last DETECTIONS reached the core
        self._alarm_lat: deque[tuple[float, float]] = deque()  # (mono, latency ms), last 60 s
        self.alarm_decisions = 0
        self.alarm_on_time = 0
        self.blind_episodes = 0
        self.blind_on_time = 0
        self._track: deque[tuple[float, float]] = deque()  # (t_capture, box width px)
        self.pending_records: list[dict[str, Any]] = []  # blind alarms, drained by run()
        self.last_decision: dict[str, Any] | None = None  # written to latest_decision.json by run()
        self.snapshot_t: str = _iso_z(time.time())  # when the AIS cache was last loaded
        # Rolling statistics: safety-check latency, frame arrivals and deadline misses.
        self._latency_ms: deque[tuple[float, float]] = deque()
        self._frame_at: deque[float] = deque()
        self._miss_at: deque[float] = deque()
        self._tick_late_ms: deque[tuple[float, float]] = deque()
        self._last_msg_mono: float | None = None
        self._last_alert_mono: float | None = None
        # Starting in OK with no message yet: the silence clock starts at the first tick.
        self._started_mono: float | None = None

    # --- AIS cache ---------------------------------------------------------

    def set_vessels(self, vessels: list[AisVessel], now_wall: float | None = None) -> None:
        """Replace the AIS cache and stamp the decision file with this snapshot time."""
        self.vessels = list(vessels)
        self.snapshot_t = _iso_z(time.time() if now_wall is None else now_wall)

    def expire_ais(self, now_wall: float) -> None:
        """Drop records older than ``ais_max_age_s``. A t of 0 means "unknown", never expired."""
        if not self.vessels:
            return
        cutoff = now_wall - self.config.ais_max_age_s
        kept = [v for v in self.vessels if v.t <= 0 or v.t >= cutoff]
        if len(kept) != len(self.vessels):
            self.vessels = kept  # the snapshot time stays the file's load time

    # --- state machine -----------------------------------------------------

    def start(self, now: float) -> None:
        self._started_mono = now
        self._last_msg_mono = now
        self._last_frame_mono = now  # the blind clock starts now: no frames yet is blind

    def on_connect(self, now: float) -> None:
        self.detector_connected = True
        self.detector_waiting = False
        self._last_msg_mono = now

    def on_disconnect(self, now: float) -> None:
        self.detector_connected = False
        self.detector_waiting = False
        self._set_state(STATE_DEGRADED, "detector link lost", now)

    def tick(self, now: float) -> None:
        """Called every loop turn. Silence longer than 3x the deadline means DEGRADED.

        It also owns the fail-safe: 500 ms without a frame raises the BLIND alarm, here, on the
        core's own tick, so it does not depend on the detector doing anything at all.
        """
        if (
            self._last_frame_mono is not None
            and not self.blind
            and now - self._last_frame_mono > BLIND_AFTER_S
        ):
            late_ms = (now - self._last_frame_mono - BLIND_AFTER_S) * 1000.0
            self.blind = True
            self.alarm_level, self.alarm_text = "blind", BLIND_TEXT
            self.blind_episodes += 1
            on_time = late_ms <= BLIND_DEADLINE_MS
            self.blind_on_time += int(on_time)
            self.pending_records.append(
                {
                    "kind": BLIND_KIND,
                    "t_wall": time.time(),
                    "latency_ms": round(late_ms, 1),
                    "deadline_ms": BLIND_DEADLINE_MS,
                    "on_time": on_time,
                }
            )
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
        if msg["type"] == protocol.HEARTBEAT:
            # Liveness only, but it may also say the source is quiet and carry the
            # detector's real scheduling class (sent once, by the first heartbeat).
            self.detector_waiting = msg.get("state") == protocol.WAITING_FOR_FRAMES
            sched = msg.get("sched")
            if isinstance(sched, dict):
                self.detector_sched = sched
            return []
        if msg["type"] != protocol.DETECTIONS:
            return []  # ALERT from a peer is not ours to act on.

        started = time.perf_counter()
        self.detector_waiting = False
        self.frames_total += 1
        self.last_frame_wall = self.last_msg_wall
        self.last_frame_age_ms = (now - msg["t_capture"]) * 1000.0
        self.last_inference_ms = (msg["t_done"] - msg["t_capture"]) * 1000.0
        if now - msg["t_capture"] > self.config.deadline_s:
            self.deadline_misses += 1
            self._miss_at.append(now)
        self._last_frame_mono = now
        self.blind = False
        decision = self.build_decision(msg)
        alarm = decision["alarm"]
        latency_ms = None
        if alarm["level"] in ("warning", "critical"):
            # Frame timestamp -> decision made: age on arrival plus the core's own processing time.
            latency_ms = (now - msg["t_capture"] + (time.perf_counter() - started)) * 1000.0
            deadline_ms = self.config.deadline_s * 1000.0
            alarm["latency_ms"] = round(latency_ms, 1)
            alarm["deadline_ms"] = round(deadline_ms)
            alarm["on_time"] = latency_ms <= deadline_ms
            self.alarm_decisions += 1
            self.alarm_on_time += int(alarm["on_time"])
            self._alarm_lat.append((now, latency_ms))
        self.alarm_level, self.alarm_text = alarm["level"], alarm.get("text", "")
        records = self._evaluate(msg, now, alarm, latency_ms)
        self.last_decision = decision
        self.last_frame_id = int(msg["frame_id"])
        self._frame_at.append(now)
        self._latency_ms.append((now, (time.perf_counter() - started) * 1000.0))
        self._prune(now)
        return records

    def note_protocol_error(self) -> None:
        self.protocol_errors += 1

    # --- rules -------------------------------------------------------------

    def nearby_vessels(self, radius_km: float) -> list[dict[str, Any]]:
        """Cached AIS vessels within ``radius_km`` of the patrol position, nearest first."""
        cfg = self.config
        dlat = radius_km / _KM_PER_DEG * 1.05
        cos_lat = max(math.cos(math.radians(cfg.unit_lat)), 0.01)
        dlon = radius_km / (_KM_PER_DEG * cos_lat) * 1.05
        found: list[dict[str, Any]] = []
        for v in self.vessels:
            if abs(v.lat - cfg.unit_lat) > dlat or abs(v.lon - cfg.unit_lon) > dlon:
                continue
            distance = haversine_km(cfg.unit_lat, cfg.unit_lon, v.lat, v.lon)
            if distance <= radius_km:
                found.append({"mmsi": _mmsi_value(v.mmsi), "name": v.name, "km": round(distance, 2)})
        found.sort(key=lambda item: item["km"])
        return found

    def nearest_vessel_km(self, radius_km: float) -> float | None:
        """Distance from the unit to the nearest cached vessel within `radius_km`, else None.

        A cheap lat/lon box pre-filter runs first; the haversine is only computed for the few
        vessels inside the box.
        """
        nearby = self.nearby_vessels(radius_km)
        return None if not nearby else float(nearby[0]["km"])

    def build_decision(self, msg: dict[str, Any]) -> dict[str, Any]:
        """The ``latest_decision.json`` object for one DETECTIONS message.

        Pure: the caller decides where (and whether) to write it. `source` is the label the
        deployment chose (``qnxcam:5``, ``net:7070``); the detector's own spec is the fallback.
        """
        cfg = self.config
        vessels = self.nearby_vessels(cfg.match_km)
        vessel_boxes = [
            box
            for box in msg["boxes"]
            if box["cls"].lower() in VESSEL_LABELS and box["score"] >= cfg.score_min
        ]
        alarm: dict[str, Any] = {"level": "none", "kind": None, "text": ""}
        best = max(vessel_boxes, key=lambda b: b["score"]) if vessel_boxes else None
        closing = self._closing(float(msg["t_capture"]), best)
        if not vessel_boxes:
            verdict = {"level": VERDICT_IDLE, "text": "No vessel in view"}
        elif vessels:
            nearest = vessels[0]
            name = str(nearest["name"] or nearest["mmsi"])
            verdict = {
                "level": VERDICT_OK,
                "text": f"Matches AIS: {name} ({float(nearest['km']):.1f} km)",
            }
        else:
            text = NOT_ON_AIS_TEXT + (" \u00b7 closing" if closing["trend"] == "closing" else "")
            verdict = {"level": VERDICT_ALERT, "text": text}
            alarm = {
                "level": "critical" if closing["trend"] == "closing" else "warning",
                "kind": ALERT_KIND,
                "text": text,
                **closing,
                "range_m": self._range_estimate_m(best),
                "estimate_note": "estimate from box size; assumes a 100 m vessel",
            }
        return {
            "t": time.time(),
            "frame_id": int(msg["frame_id"]),
            "source": cfg.source if cfg.source and cfg.source != "unknown" else msg.get("source", "unknown"),
            "ai": {
                "engine": str(msg.get("engine", "unknown")),
                "inference_ms": round((msg["t_done"] - msg["t_capture"]) * 1000.0, 1),
                "boxes": [
                    {
                        "label": str(box["cls"]),
                        "score": round(float(box["score"]), 3),
                        "xyxy": [box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]],
                    }
                    for box in msg["boxes"]
                ],
            },
            "patrol": {"lat": cfg.unit_lat, "lon": cfg.unit_lon, "name": cfg.patrol_name},
            "ais": {"nearby": vessels, "radius_km": cfg.match_km, "snapshot_t": self.snapshot_t},
            "verdict": verdict,
            "alarm": alarm,
        }

    def _range_estimate_m(self, box: dict[str, Any] | None) -> int | None:
        """Pinhole estimate: range = assumed length x focal length / box width. Rough, by design."""
        if box is None or float(box["w"]) <= 1.0:
            return None
        focal_px = self.config.frame_width_px / (2.0 * math.tan(math.radians(CAMERA_HFOV_DEG / 2.0)))
        return int(min(20000.0, max(10.0, ASSUMED_VESSEL_M * focal_px / float(box["w"]))) // 10 * 10)

    def _closing(self, t_capture: float, box: dict[str, Any] | None) -> dict[str, Any]:
        """Closing / opening / steady from how the box width changed over the last 0.5-2 s.

        Time to collision is the scale rule tau = w / (dw/dt): it needs no vessel size, only the
        growth. It is an estimate for a bridge to weigh, not a closest-point-of-approach claim.
        """
        track = self._track
        if box is None:
            if track and t_capture - track[-1][0] > 1.0:
                track.clear()
            return {"trend": None, "ttc_s": None}
        track.append((t_capture, float(box["w"])))
        while track and t_capture - track[0][0] > _TRACK_WINDOW_S:
            track.popleft()
        t0, w0 = track[0]
        dt, w = t_capture - t0, float(box["w"])
        if len(track) < 3 or dt < 0.5 or w0 <= 1.0:
            return {"trend": None, "ttc_s": None}
        ratio = w / w0
        if ratio >= CLOSING_RATIO:
            return {"trend": "closing", "ttc_s": round(dt * w / (w - w0), 1)}
        return {"trend": "opening" if ratio <= OPENING_RATIO else "steady", "ttc_s": None}

    def build_blind_decision(self) -> dict[str, Any]:
        """The decision file while the AI is down: the safety core still answers, and says so.

        What stays true without the detector: the 20 Hz loop, the heartbeat watch, the AIS
        cross-check (ships broadcasting near the patrol position are still listed) and this
        verdict. What is lost: seeing ships that are not broadcasting. So the verdict asks
        for a human on watch instead of reporting "no vessel in view".
        """
        cfg = self.config
        return {
            "t": time.time(),
            "frame_id": self.last_frame_id,
            "source": cfg.source if cfg.source and cfg.source != "unknown" else "unknown",
            "ai": {"engine": "down", "inference_ms": None, "boxes": [], "down": True},
            "patrol": {"lat": cfg.unit_lat, "lon": cfg.unit_lon, "name": cfg.patrol_name},
            "ais": {
                "nearby": self.nearby_vessels(cfg.match_km),
                "radius_km": cfg.match_km,
                "snapshot_t": self.snapshot_t,
            },
            "verdict": {"level": VERDICT_ALERT, "text": BLIND_TEXT},
            "alarm": {"level": "blind", "kind": BLIND_KIND, "text": BLIND_TEXT},
        }

    def _evaluate(
        self,
        msg: dict[str, Any],
        now: float,
        alarm: dict[str, Any] | None = None,
        latency_ms: float | None = None,
    ) -> list[dict[str, Any]]:
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
            "frame_sha256": msg.get("frame_sha256"),
            "latency_ms": None if latency_ms is None else round(latency_ms, 1),
            "deadline_ms": round(self.config.deadline_s * 1000),
            "on_time": None if latency_ms is None else latency_ms <= self.config.deadline_s * 1000,
            "trend": (alarm or {}).get("trend"),
            "ttc_s": (alarm or {}).get("ttc_s"),
            "range_m": (alarm or {}).get("range_m"),
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

    def note_tick(self, late_ms: float, now: float) -> None:
        """Record how late one safety-core housekeeping tick started (monotonic clock)."""
        self._tick_late_ms.append((now, max(0.0, float(late_ms))))

    def _prune(self, now: float) -> None:
        """Drop samples older than the statistics window (called on every frame/status)."""
        for series in (self._frame_at, self._miss_at):
            while series and now - series[0] > _STATS_WINDOW_S:
                series.popleft()
        while self._latency_ms and now - self._latency_ms[0][0] > _STATS_WINDOW_S:
            self._latency_ms.popleft()
        while self._tick_late_ms and now - self._tick_late_ms[0][0] > _STATS_WINDOW_S:
            self._tick_late_ms.popleft()
        while self._alarm_lat and now - self._alarm_lat[0][0] > _STATS_WINDOW_S:
            self._alarm_lat.popleft()

    def _tick_stats(self, now: float) -> dict[str, Any]:
        """Lateness of the core's own 20 Hz loop (not end-to-end frame time)."""
        self._prune(now)
        lateness = [late for _, late in self._tick_late_ms]
        p50 = _percentile(lateness, 0.50)
        p99 = _percentile(lateness, 0.99)
        hz = self.config.tick_hz
        return {
            "hz": int(hz) if float(hz).is_integer() else round(hz, 3),
            "p50_late_ms": 0.0 if p50 is None else round(p50, 3),
            "p99_late_ms": 0.0 if p99 is None else round(p99, 3),
            "ticks_60s": len(lateness),
            "max_late_ms": 0.0 if not lateness else round(max(lateness), 3),
            "late_count_60s": sum(1 for late in lateness if late > LATE_TOLERANCE_MS),
        }

    def _alarm_stats(self, now: float) -> dict[str, Any]:
        """The guarantee, as numbers: how many alarms met their deadline and how fast they were."""
        self._prune(now)
        lat = [ms for _, ms in self._alarm_lat]
        p99 = _percentile(lat, 0.99)
        return {
            "level": self.alarm_level,
            "text": self.alarm_text,
            "deadline_ms": round(self.config.deadline_s * 1000),
            "decisions": self.alarm_decisions,
            "on_time": self.alarm_on_time,
            "p99_latency_ms": None if p99 is None else round(p99, 1),
            "max_latency_ms": None if not lat else round(max(lat), 1),
            "blind": self.blind,
            "blind_episodes": self.blind_episodes,
            "blind_on_time": self.blind_on_time,
            "blind_deadline_ms": BLIND_DEADLINE_MS,
        }

    def _stats(self, now: float) -> dict[str, Any]:
        """Per-second and per-minute numbers the console shows for real-time behaviour."""
        self._prune(now)
        recent_frames = sum(1 for at in self._frame_at if now - at <= _FRAME_RATE_WINDOW_S)
        latencies = [ms for _, ms in self._latency_ms]
        return {
            "frames_per_s": round(recent_frames / _FRAME_RATE_WINDOW_S, 1),
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p99_ms": _percentile(latencies, 0.99),
            "latency_samples": len(latencies),
            "deadline_misses_60s": len(self._miss_at),
        }

    def status(
        self,
        *,
        priority: str,
        ais_error: str | None,
        now_wall: float,
        now_mono: float | None = None,
        sched: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = self.config
        now = time.monotonic() if now_mono is None else now_mono
        return {
            "component": "safety_core",
            "pid": os.getpid(),
            "t_wall": now_wall,
            "state": self.state,
            "state_reason": self.state_reason,
            "state_since_wall": self.state_since_wall,
            "priority": priority,
            "sched": sched or {},
            "detector_connected": self.detector_connected,
            "detector_waiting": self.detector_waiting,
            "detector_sched": self.detector_sched,
            "frames_total": self.frames_total,
            "last_msg_wall": self.last_msg_wall,
            "last_frame_wall": self.last_frame_wall,
            "deadline_ms": round(cfg.deadline_s * 1000),
            "silence_ms": round(cfg.silence_s * 1000),
            "deadline_misses": self.deadline_misses,
            **self._stats(now),
            "protocol_errors": self.protocol_errors,
            "last_frame_age_ms": _round_or_none(self.last_frame_age_ms),
            "last_inference_ms": _round_or_none(self.last_inference_ms),
            "alerts_total": self.alerts_total,
            "alerts_suppressed": self.alerts_suppressed,
            "last_alert": self.last_alert,
            "ais_records": len(self.vessels),
            "ais_error": ais_error,
            "safety_tick": self._tick_stats(now),
            "alarm": self._alarm_stats(now),
            "transitions": self.transitions,
        }


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _mmsi_value(mmsi: str) -> int | str:
    """The contract shows an integer MMSI; keep a non-numeric identifier as a string."""
    return int(mmsi) if mmsi.isdigit() else mmsi


def _iso_z(wall: float) -> str:
    """A UTC second-resolution stamp, e.g. ``2026-11-21T15:31:00Z``."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(wall))


def _percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile (no interpolation, so a p99 is always a real sample)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[rank], 3)


class _AlertLog:
    """Append-only JSONL. Each record is fsynced: an alert must survive a power cut.

    The fsync takes ~100 ms on the Pi's disk, so it runs on a side thread: the 20 Hz loop only
    enqueues. ``close`` drains the queue, so nothing queued is lost on a clean stop.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._queue: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="alert-log", daemon=True)
        self._thread.start()

    def append(self, record: dict[str, Any]) -> None:
        self._queue.put(record)

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            if record is None:
                return
            try:
                self._write(record)
            except OSError:
                pass  # a full disk must not stop the safety core; the alert is still in status

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=3.0)


class _LatestWriter:
    """Writes JSON files on a side thread so the 20 Hz loop never waits on the filesystem.

    Measured on the QNX Pi: with the camera streaming, one atomic file replace took up to 160 ms,
    which made the safety loop itself late. Only the newest object per path is kept; an older one
    that was never written is simply superseded (the files are "latest state", not a log).
    """

    def __init__(self, on_idle: Callable[[], None] | None = None) -> None:
        # on_idle runs about once a second on this thread: filesystem polling (the AIS file's
        # mtime) belongs here too. A stat() on the Pi's disk stalled the loop for up to 90 ms.
        self._on_idle = on_idle
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._wake = threading.Event()
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="safety-writer", daemon=True)
        self._thread.start()

    def submit(self, path: str | None, obj: dict[str, Any]) -> None:
        if not path:
            return
        with self._lock:
            self._pending[path] = obj
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._on_idle is not None:
                try:
                    self._on_idle()
                except Exception:  # a bad AIS file keeps the previous cache; never kill the thread
                    pass
            with self._lock:
                batch, self._pending = self._pending, {}
                closing = self._closing
            for path, obj in batch.items():
                try:
                    _write_json_atomic(path, obj)
                except OSError:
                    pass  # a read-only state dir must not kill the core
            if closing and not self._pending:
                return

    def close(self) -> None:
        """Flush what is queued and stop the thread (bounded wait)."""
        with self._lock:
            self._closing = True
        self._wake.set()
        self._thread.join(timeout=2.0)


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
        self._changed = False

    def poll(self) -> None:
        """Side-thread entry: reload when the file changed and remember that it did."""
        if self.refresh():
            self._changed = True

    def take_changed(self) -> bool:
        changed, self._changed = self._changed, False
        return changed

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
    if config.set_priority:
        elevated, sched = realtime.try_fifo(config.fifo_priority)
        if elevated:
            priority = realtime.describe(sched)
            print(f"safety core priority: {priority}", file=sys.stderr, flush=True)
        else:
            priority = realtime.describe(sched)
            print(
                f"safety core: SCHED_FIFO {config.fifo_priority} unavailable "
                f"({priority}); continuing at normal priority",
                file=sys.stderr,
                flush=True,
            )
    else:
        sched = realtime.current_schedule()
        priority = "not requested"
        print(
            f"safety core priority: not requested ({realtime.describe(sched)})", file=sys.stderr, flush=True
        )

    ais = _AisCache(config.ais_path)
    ais.refresh()
    core = SafetyCore(config, ais.vessels)
    core.snapshot_t = _iso_z(time.time())
    alerts = _AlertLog(config.alerts_path)
    sightings = SightingsLog(config.sightings_path) if config.sightings_path else None
    writer = _LatestWriter(on_idle=ais.poll)
    listener = _listen(config.socket_path)
    client: socket.socket | None = None
    decoder = protocol.MessageDecoder()

    start = time.monotonic()
    core.start(start)
    period = 1.0 / config.tick_hz if config.tick_hz > 0 else _SELECT_MAX_S
    next_tick = start + period
    last_status = 0.0
    last_blind = 0.0
    now = start
    if on_ready is not None:
        on_ready(core)
    try:
        while stop is None or not stop.is_set():
            now = time.monotonic()
            if duration_s is not None and now - start >= duration_s:
                break
            timeout = max(0.0, min(next_tick - now, _SELECT_MAX_S))
            rlist = [listener] if client is None else [listener, client]
            readable, _, _ = select.select(rlist, [], [], timeout)
            now = time.monotonic()

            if listener in readable:
                new_client = _accept(listener, client, core, now)
                if new_client is not client:
                    client = new_client
                    decoder = protocol.MessageDecoder()  # a new link starts with an empty buffer
            if client is not None and client in readable:
                client, closed = _read(client, decoder, core, alerts, writer, sightings, now)
                if closed:
                    core.on_disconnect(now)

            # The housekeeping tick runs on absolute deadlines, so a late wake-up does not
            # shift the whole schedule. Its lateness is what `safety_tick` reports.
            if now >= next_tick:
                core.note_tick((now - next_tick) * 1000.0, now)
                core.tick(now)
                core.expire_ais(time.time())
                for record in core.pending_records:
                    alerts.append(record)
                core.pending_records.clear()
                if core.blind and now - last_blind >= 0.1:
                    last_blind = now
                    writer.submit(config.decision_path, core.build_blind_decision())
                while next_tick <= now:
                    next_tick += period

            if now - last_status >= config.status_every_s:
                if ais.take_changed():
                    core.set_vessels(ais.vessels)
                last_status = now
                writer.submit(
                    config.status_path,
                    core.status(
                        priority=priority,
                        ais_error=ais.error,
                        now_wall=time.time(),
                        now_mono=now,
                        sched=sched,
                    ),
                )
    finally:
        writer.close()
        alerts.close()
        if sightings is not None:
            sightings.close()
        if client is not None:
            client.close()
        listener.close()
        try:
            os.unlink(config.socket_path)
        except OSError:
            pass
        _write_json_atomic(
            config.status_path,
            core.status(
                priority=priority,
                ais_error=ais.error,
                now_wall=time.time(),
                now_mono=now,
                sched=sched,
            ),
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


def _sighting(core: SafetyCore, record: dict[str, Any]) -> dict[str, Any]:
    """What the log keeps of one not-on-AIS alert (all of it comes from the alert record)."""
    cfg = core.config
    return {
        "kind": "patrol_sighting",
        "frame_id": record.get("frame_id"),
        "frame_sha256": record.get("frame_sha256"),
        "patrol": {"lat": cfg.unit_lat, "lon": cfg.unit_lon, "name": cfg.patrol_name},
        "verdict": NOT_ON_AIS_TEXT,
        "confidence": record.get("confidence"),
        "trend": record.get("trend"),
        "range_m": record.get("range_m"),
        "latency_ms": record.get("latency_ms"),
        "on_time": record.get("on_time"),
    }


def _read(
    client: socket.socket,
    decoder: protocol.MessageDecoder,
    core: SafetyCore,
    alerts: _AlertLog,
    writer: _LatestWriter,
    sightings: SightingsLog | None,
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
            if sightings is not None and record.get("kind") == ALERT_KIND:
                sightings.submit(_sighting(core, record))
        if msg["type"] == protocol.DETECTIONS and core.last_decision is not None:
            writer.submit(core.config.decision_path, core.last_decision)
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
    parser.add_argument("--patrol-name", default="patrol", help="name shown in the decision file")
    parser.add_argument("--sightings", default=None, help="hash-chained sightings log (JSONL)")
    parser.add_argument("--source", default="unknown", help='decision source label, e.g. "qnxcam:5"')
    parser.add_argument("--score-min", type=float, default=0.3, help="minimum vessel score for a verdict")
    parser.add_argument(
        "--decision", default=None, help="latest_decision.json path (default: beside the status file)"
    )
    parser.add_argument("--tick-hz", type=float, default=TICK_HZ)
    parser.add_argument("--no-priority", action="store_true", help="do not change scheduling")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds (tests, demos)")
    args = parser.parse_args(argv)

    decision_path = args.decision
    if decision_path is None:
        decision_path = os.path.join(os.path.dirname(os.path.abspath(args.status)), DECISION_NAME)
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
        decision_path=decision_path,
        patrol_name=args.patrol_name,
        source=args.source,
        score_min=args.score_min,
        sightings_path=args.sightings,
        tick_hz=args.tick_hz,
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
