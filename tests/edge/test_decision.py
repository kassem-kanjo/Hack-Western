"""Decision file contract, verdicts and the safety core's own 20 Hz tick lateness."""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time

import pytest

from darkfleet.edge import protocol
from darkfleet.edge.safety_core import (
    AisVessel,
    CoreConfig,
    SafetyCore,
    _iso_z,
    run,
)

UNIT_LAT, UNIT_LON = 26.57, 56.25


def _config(**overrides) -> CoreConfig:
    base = dict(
        socket_path="/tmp/unused.sock",
        status_path="/tmp/unused-status.json",
        alerts_path="/tmp/unused-alerts.jsonl",
        unit_lat=UNIT_LAT,
        unit_lon=UNIT_LON,
        match_km=2.0,
        deadline_s=0.2,
        alert_cooldown_s=0.0,
        set_priority=False,
        patrol_name="Strait of Hormuz patrol",
        source="qnxcam:5",
        score_min=0.3,
    )
    base.update(overrides)
    return CoreConfig(**base)


def _frame(
    frame_id: int = 1,
    t_capture: float = 0.5,
    boxes: list[dict] | None = None,
    engine: str = "opencv-dnn 4.12.0",
) -> dict:
    if boxes is None:
        boxes = [{"cls": "boat", "score": 0.82, "x": 10.0, "y": 20.0, "w": 30.0, "h": 15.0}]
    return protocol.make_detections(
        frame_id, t_capture, t_capture + 0.172, boxes, engine=engine, source="qnxfile:/tmp/qnxcam.frame"
    )


def _vessel(km: float, name: str, mmsi: str = "219000001") -> AisVessel:
    return AisVessel(mmsi=mmsi, lat=UNIT_LAT + km / 111.19, lon=UNIT_LON, t=0.0, name=name)


def test_decision_contract_shape_and_alert_verdict() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)

    core.on_message(_frame(), now=0.52)

    decision = core.last_decision
    assert decision is not None
    assert set(decision) == {"t", "frame_id", "source", "ai", "patrol", "ais", "verdict", "alarm"}
    assert decision["frame_id"] == 1
    assert decision["source"] == "qnxcam:5"  # the deployment label beats the detector's spec
    assert decision["ai"]["engine"] == "opencv-dnn 4.12.0"
    assert decision["ai"]["inference_ms"] == pytest.approx(172.0, abs=0.1)
    assert decision["ai"]["boxes"] == [{"label": "boat", "score": 0.82, "xyxy": [10.0, 20.0, 40.0, 35.0]}]
    assert decision["patrol"] == {
        "lat": UNIT_LAT,
        "lon": UNIT_LON,
        "name": "Strait of Hormuz patrol",
    }
    assert decision["ais"]["radius_km"] == 2.0
    assert decision["ais"]["nearby"] == []
    assert decision["ais"]["snapshot_t"].endswith("Z")
    assert decision["verdict"] == {"level": "alert", "text": "VESSEL NOT ON AIS \u2014 alert bridge"}


def test_decision_ok_when_the_nearest_ais_vessel_matches() -> None:
    core = SafetyCore(_config(), vessels=[_vessel(1.2, "NORDIC FURY")])
    core.start(0.0)

    core.on_message(_frame(), now=0.52)

    decision = core.last_decision
    assert decision is not None
    assert decision["ais"]["nearby"] == [{"mmsi": 219000001, "name": "NORDIC FURY", "km": 1.2}]
    assert decision["verdict"] == {"level": "ok", "text": "Matches AIS: NORDIC FURY (1.2 km)"}


def test_decision_idle_without_a_vessel_box() -> None:
    person = {"cls": "person", "score": 0.99, "x": 1.0, "y": 1.0, "w": 5.0, "h": 5.0}
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)

    core.on_message(_frame(boxes=[]), now=0.52)
    assert core.last_decision is not None
    assert core.last_decision["verdict"] == {"level": "idle", "text": "No vessel in view"}

    core.on_message(_frame(frame_id=2, boxes=[person]), now=0.6)
    assert core.last_decision is not None
    assert core.last_decision["verdict"]["level"] == "idle"


def test_decision_ignores_low_score_and_accepts_the_ship_label() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)

    low = {"cls": "boat", "score": 0.2, "x": 1.0, "y": 1.0, "w": 5.0, "h": 5.0}
    core.on_message(_frame(boxes=[low]), now=0.52)
    assert core.last_decision is not None
    assert core.last_decision["verdict"]["level"] == "idle"

    ship = {"cls": "ship", "score": 0.9, "x": 1.0, "y": 1.0, "w": 5.0, "h": 5.0}
    core.on_message(_frame(frame_id=2, boxes=[ship]), now=0.6)
    assert core.last_decision is not None
    assert core.last_decision["verdict"]["level"] == "alert"


def test_tick_stats_report_lateness_and_the_late_count() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)

    core.note_tick(1.0, 1.0)
    core.note_tick(10.0, 1.05)
    core.note_tick(50.0, 1.1)
    status = core.status(priority="test", ais_error=None, now_wall=1.1, now_mono=1.1)

    tick = status["safety_tick"]
    assert tick["hz"] == 20
    assert tick["max_late_ms"] == 50.0
    assert tick["p99_late_ms"] == 50.0
    assert tick["late_count_60s"] == 2  # 1 ms is inside the tolerance; 10 and 50 ms are not


def test_tick_samples_leave_the_minute_window() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    core.note_tick(100.0, 1.0)

    later = core.status(priority="test", ais_error=None, now_wall=70.0, now_mono=70.0)
    assert later["safety_tick"] == {
        "hz": 20,
        "p50_late_ms": 0.0,
        "p99_late_ms": 0.0,
        "max_late_ms": 0.0,
        "late_count_60s": 0,
        "ticks_60s": 0,
    }


def test_ais_expiry_drops_old_records_but_keeps_unknown_times() -> None:
    old = AisVessel(mmsi="1", lat=UNIT_LAT, lon=UNIT_LON, t=1_000.0)
    fresh = AisVessel(mmsi="2", lat=UNIT_LAT, lon=UNIT_LON, t=1_000.0)
    unknown = AisVessel(mmsi="3", lat=UNIT_LAT, lon=UNIT_LON, t=0.0)
    core = SafetyCore(_config(ais_max_age_s=60.0), vessels=[old, fresh, unknown])

    core.expire_ais(now_wall=1_100.0)

    assert [v.mmsi for v in core.vessels] == ["3"]  # both timed records are older than 60 s


def test_iso_z_is_utc_to_the_second() -> None:
    assert _iso_z(0.0) == "1970-01-01T00:00:00Z"


def _connect(path: str, deadline: float = 5.0) -> socket.socket:
    end = time.monotonic() + deadline
    while True:
        cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            cli.connect(path)
            return cli
        except (FileNotFoundError, ConnectionRefusedError):
            cli.close()
            if time.monotonic() > end:
                raise
            time.sleep(0.02)


def test_run_writes_the_decision_file_after_a_frame(tmp_path) -> None:
    sock_dir = tempfile.mkdtemp(prefix="dfd-", dir="/tmp")
    try:
        decision_path = tmp_path / "latest_decision.json"
        cfg = _config(
            socket_path=os.path.join(sock_dir, "d.sock"),
            status_path=str(tmp_path / "safety_status.json"),
            alerts_path=str(tmp_path / "alerts.jsonl"),
            decision_path=str(decision_path),
            deadline_s=0.05,
            status_every_s=0.05,
        )
        stop = threading.Event()
        errors: list[BaseException] = []

        def _serve() -> None:
            try:
                run(cfg, stop=stop, duration_s=20)
            except BaseException as exc:  # surface in the main thread
                errors.append(exc)

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        try:
            cli = _connect(cfg.socket_path)
            cli.settimeout(5)
            protocol.send_msg(cli, _frame(frame_id=9, t_capture=time.monotonic()))
            end = time.monotonic() + 5.0
            while time.monotonic() < end:
                try:
                    decision = json.loads(decision_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    decision = None
                if decision is not None:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("latest_decision.json was never written")
            assert decision["frame_id"] == 9
            assert decision["source"] == "qnxcam:5"
            assert decision["verdict"]["level"] == "alert"
            cli.close()

            status = json.loads((tmp_path / "safety_status.json").read_text(encoding="utf-8"))
            assert status["safety_tick"]["hz"] == 20
            assert status["safety_tick"]["late_count_60s"] >= 0
        finally:
            stop.set()
            thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors, errors
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_blind_decision_says_the_ai_is_down_and_keeps_the_ais_check() -> None:
    near = AisVessel(mmsi="219000001", name="NEAR", lat=UNIT_LAT + 0.005, lon=UNIT_LON, t=0.0)
    core = SafetyCore(_config(), [near])
    decision = core.build_blind_decision()
    assert decision["verdict"]["level"] == "alert"
    assert "LOOKOUT BLIND" in decision["verdict"]["text"]
    assert decision["alarm"]["level"] == "blind"
    assert decision["ai"]["down"] is True and decision["ai"]["boxes"] == []
    assert [v["name"] for v in decision["ais"]["nearby"]] == ["NEAR"]


def _boxed_frame(frame_id: int, t_capture: float, width: float) -> dict:
    return _frame(
        frame_id=frame_id,
        t_capture=t_capture,
        boxes=[{"cls": "boat", "score": 0.8, "x": 100.0, "y": 100.0, "w": width, "h": width / 2}],
    )


def test_not_on_ais_alarm_has_latency_and_deadline() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    core.on_message(_boxed_frame(1, 0.5, 100.0), now=0.62)
    alarm = core.last_decision["alarm"]
    assert alarm["level"] == "warning" and alarm["kind"] == "not_on_ais"
    assert alarm["on_time"] is True and 100 <= alarm["latency_ms"] < 200
    assert alarm["range_m"] is not None and "estimate" in alarm["estimate_note"]
    # a frame that reaches the core already 250 ms old misses the 200 ms deadline, and says so
    core.on_message(_boxed_frame(2, 0.6, 100.0), now=0.85)
    assert core.last_decision["alarm"]["on_time"] is False
    status = core.status(priority="t", ais_error=None, now_wall=1.0, now_mono=0.9)
    assert status["alarm"]["decisions"] == 2 and status["alarm"]["on_time"] == 1


def test_growing_box_is_closing_and_critical() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    for i, width in enumerate((100.0, 104.0, 110.0, 118.0, 130.0)):
        t = 0.5 + 0.2 * i
        core.on_message(_boxed_frame(i, t, width), now=t + 0.1)
    alarm = core.last_decision["alarm"]
    assert alarm["trend"] == "closing" and alarm["level"] == "critical"
    assert alarm["ttc_s"] is not None and alarm["ttc_s"] > 0
    assert "closing" in core.last_decision["verdict"]["text"]


def test_shrinking_box_is_opening_and_a_matching_ais_ship_clears_the_alarm() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    for i, width in enumerate((130.0, 120.0, 112.0, 104.0, 98.0)):
        t = 0.5 + 0.2 * i
        core.on_message(_boxed_frame(i, t, width), now=t + 0.1)
    assert core.last_decision["alarm"]["trend"] == "opening"
    assert core.last_decision["alarm"]["level"] == "warning"
    matched = SafetyCore(_config(), vessels=[_vessel(1.0, "ON AIS")])
    matched.start(0.0)
    matched.on_message(_boxed_frame(1, 0.5, 100.0), now=0.6)
    assert matched.last_decision["alarm"]["level"] == "none"


def test_no_frame_for_500_ms_raises_lookout_blind_on_the_next_tick() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    core.on_message(_frame(), now=0.1)
    core.tick(0.55)
    assert not core.blind
    core.tick(0.65)
    assert core.blind and core.alarm_level == "blind"
    assert core.blind_episodes == 1 and core.blind_on_time == 1  # 50 ms past the threshold
    assert core.pending_records[0]["kind"] == "lookout_blind"
    core.on_message(_frame(frame_id=2, t_capture=0.9), now=0.95)  # frames are back
    assert not core.blind and core.alarm_level != "blind"


def test_a_late_blind_tick_is_counted_as_a_miss() -> None:
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    core.tick(1.0)  # the loop woke 500 ms after the threshold: far past the 100 ms budget
    assert core.blind_episodes == 1 and core.blind_on_time == 0
