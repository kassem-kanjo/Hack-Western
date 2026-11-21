"""Safety core: alert rule, deadline clock, DEGRADED/OK state machine, and the socket loop."""

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
    ALERT_KIND,
    STATE_DEGRADED,
    STATE_OK,
    AisVessel,
    CoreConfig,
    SafetyCore,
    haversine_km,
    load_ais_snapshot,
    run,
)

UNIT_LAT, UNIT_LON = 55.0, 19.0


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
    )
    base.update(overrides)
    return CoreConfig(**base)


def _boat_frame(frame_id: int, t_capture: float, score: float = 0.9) -> dict:
    return protocol.make_detections(
        frame_id,
        t_capture,
        t_capture + 0.02,
        [{"cls": "boat", "score": score, "x": 10.0, "y": 20.0, "w": 30.0, "h": 15.0}],
    )


def _vessel_km_north(km: float) -> AisVessel:
    """A vessel `km` kilometres due north of the unit."""
    dlat = km / 111.19
    return AisVessel(mmsi="219000001", lat=UNIT_LAT + dlat, lon=UNIT_LON, t=0.0)


def test_haversine_one_degree_of_latitude_is_about_111_km():
    assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.2, abs=0.2)


def test_alert_raised_when_no_ais_vessel_is_near():
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    alerts = core.on_message(_boat_frame(1, 0.5, score=0.83), now=0.52)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["type"] == protocol.ALERT
    assert alert["kind"] == ALERT_KIND
    assert alert["confidence"] == pytest.approx(0.83)
    assert alert["frame_id"] == 1
    assert alert["nearest_ais_km"] is None
    assert alert["t_wall"] > 0


def test_no_alert_when_ais_vessel_is_within_match_km():
    core = SafetyCore(_config(), vessels=[_vessel_km_north(1.0)])
    core.start(0.0)
    assert core.on_message(_boat_frame(1, 0.5), now=0.52) == []
    assert core.alerts_total == 0


def test_alert_raised_when_nearest_ais_vessel_is_beyond_match_km():
    core = SafetyCore(_config(), vessels=[_vessel_km_north(5.0)])
    core.start(0.0)
    alerts = core.on_message(_boat_frame(1, 0.5), now=0.52)
    assert len(alerts) == 1
    assert alerts[0]["nearest_ais_km"] == pytest.approx(5.0, abs=0.05)


def test_no_alert_without_a_boat_box():
    core = SafetyCore(_config(), vessels=[])
    core.start(0.0)
    empty = protocol.make_detections(2, 0.5, 0.52, [])
    person_only = protocol.make_detections(
        3, 0.5, 0.52, [{"cls": "person", "score": 0.99, "x": 1, "y": 1, "w": 5, "h": 5}]
    )
    assert core.on_message(empty, now=0.52) == []
    assert core.on_message(person_only, now=0.52) == []


def test_cooldown_suppresses_repeat_alerts():
    core = SafetyCore(_config(alert_cooldown_s=5.0), vessels=[])
    core.start(0.0)
    assert len(core.on_message(_boat_frame(1, 0.5), now=0.52)) == 1
    assert core.on_message(_boat_frame(2, 0.6), now=0.62) == []
    assert core.alerts_suppressed == 1


def test_deadline_miss_counted_when_frame_is_old_on_arrival():
    core = SafetyCore(_config(deadline_s=0.2), vessels=[_vessel_km_north(1.0)])
    core.start(0.0)
    core.on_message(_boat_frame(1, t_capture=0.0), now=0.25)  # 250 ms old: a miss
    core.on_message(_boat_frame(2, t_capture=0.3), now=0.35)  # 50 ms old: on time
    assert core.deadline_misses == 1
    assert core.frames_total == 2


def test_status_reports_frames_per_second_and_check_latency():
    core = SafetyCore(_config(), vessels=[_vessel_km_north(1.0)])
    core.start(0.0)
    for frame_id, now in enumerate([1.0, 1.1, 1.2, 1.3]):
        core.on_message(_boat_frame(frame_id, t_capture=now - 0.01), now=now)

    status = core.status(priority="test", ais_error=None, now_wall=1.0, now_mono=1.35)

    assert status["frames_per_s"] == 4.0  # four frames inside the one-second window
    assert status["latency_samples"] == 4
    assert status["latency_p50_ms"] is not None
    assert status["latency_p99_ms"] is not None
    assert status["latency_p99_ms"] >= status["latency_p50_ms"] >= 0.0


def test_deadline_misses_are_reported_total_and_for_the_last_minute():
    core = SafetyCore(_config(deadline_s=0.2), vessels=[_vessel_km_north(1.0)])
    core.start(0.0)
    core.on_message(_boat_frame(1, t_capture=0.0), now=0.5)  # a miss at t=0.5

    fresh = core.status(priority="test", ais_error=None, now_wall=1.0, now_mono=0.6)
    assert fresh["deadline_misses"] == 1
    assert fresh["deadline_misses_60s"] == 1

    later = core.status(priority="test", ais_error=None, now_wall=61.0, now_mono=61.0)
    assert later["deadline_misses"] == 1  # the total never forgets
    assert later["deadline_misses_60s"] == 0  # the minute window does
    assert later["frames_per_s"] == 0.0


def test_heartbeat_reports_waiting_state_and_the_detector_schedule():
    core = SafetyCore(_config(deadline_s=0.2), vessels=[])
    core.start(0.0)
    sched = {"policy": "SCHED_OTHER", "priority": 0, "nice": 10}

    core.on_message(protocol.make_heartbeat(0.1, state=protocol.WAITING_FOR_FRAMES, sched=sched), now=0.1)

    assert core.detector_waiting is True
    assert core.detector_sched == sched
    assert core.state == STATE_OK  # a waiting detector is alive, not degraded

    core.on_message(_boat_frame(1, t_capture=0.2), now=0.21)
    assert core.detector_waiting is False  # a frame means the wait is over

    core.on_message(protocol.make_heartbeat(0.3, state=protocol.WAITING_FOR_FRAMES), now=0.3)
    assert core.detector_waiting is True
    core.on_disconnect(0.4)
    assert core.detector_waiting is False  # a dead link is never "waiting"


def test_degraded_after_three_deadlines_of_silence_then_ok_on_resume():
    core = SafetyCore(_config(deadline_s=0.2), vessels=[])  # silence_s = 0.6
    core.start(0.0)
    core.tick(0.5)
    assert core.state == STATE_OK
    core.tick(0.7)
    assert core.state == STATE_DEGRADED
    assert "no detector message" in core.state_reason
    core.on_message(protocol.make_heartbeat(0.8), now=0.8)
    assert core.state == STATE_OK
    assert [t["state"] for t in core.transitions] == [STATE_DEGRADED, STATE_OK]


def test_degraded_still_applies_the_alert_rule_when_frames_resume():
    core = SafetyCore(_config(deadline_s=0.2), vessels=[])  # empty cache: every boat alerts
    core.start(0.0)
    core.tick(1.0)
    assert core.state == STATE_DEGRADED
    alerts = core.on_message(_boat_frame(1, 1.0), now=1.01)
    assert len(alerts) == 1
    assert core.state == STATE_OK


def test_load_ais_snapshot_skips_bad_records_and_rejects_non_list(tmp_path):
    good = {"mmsi": 219000001, "lat": 55.1, "lon": 19.2, "t": 1700000000}
    bad_lat = {"mmsi": 1, "lat": 123.0, "lon": 0.0, "t": 0}
    path = tmp_path / "ais.json"
    path.write_text(json.dumps([good, bad_lat, {"mmsi": 2}]))
    vessels = load_ais_snapshot(str(path))
    assert [v.mmsi for v in vessels] == ["219000001"]
    path.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError):
        load_ais_snapshot(str(path))
    assert load_ais_snapshot(str(tmp_path / "missing.json")) == []


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


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_socket_loop_alerts_degrades_recovers_and_persists(tmp_path):
    """The real serve loop: a detector connects, sends a boat, goes silent, then resumes."""
    sock_dir = tempfile.mkdtemp(prefix="dfs-", dir="/tmp")
    try:
        cfg = _config(
            socket_path=os.path.join(sock_dir, "c.sock"),
            status_path=str(tmp_path / "safety_status.json"),
            alerts_path=str(tmp_path / "alerts.jsonl"),
            deadline_s=0.05,  # silence after 150 ms
            status_every_s=0.05,
        )
        stop = threading.Event()
        errors: list[BaseException] = []

        def _serve():
            try:
                run(cfg, stop=stop, duration_s=20)
            except BaseException as exc:  # surface in the main thread
                errors.append(exc)

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        try:
            # Status keeps updating with no detector at all: the core never blocks on it.
            t1 = _wait_status(cfg.status_path, lambda s: s.get("t_wall"))["t_wall"]
            time.sleep(0.2)
            t2 = _read_json(cfg.status_path)["t_wall"]
            assert t2 > t1

            cli = _connect(cfg.socket_path)
            cli.settimeout(5)
            protocol.send_msg(cli, _boat_frame(7, time.monotonic()))
            _wait_status(cfg.status_path, lambda s: s.get("alerts_total") == 1 or None)

            # Silence: DEGRADED, reported through the status file.
            _wait_status(cfg.status_path, lambda s: s.get("state") == STATE_DEGRADED or None)

            # Resume: OK again.
            protocol.send_msg(cli, protocol.make_heartbeat())
            _wait_status(cfg.status_path, lambda s: s.get("state") == STATE_OK or None)

            cli.close()
            lines = open(cfg.alerts_path, encoding="utf-8").read().splitlines()
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["kind"] == ALERT_KIND
            assert record["frame_id"] == 7
        finally:
            stop.set()
            thread.join(timeout=5)
        assert not thread.is_alive()
        assert not errors, errors
        assert not os.path.exists(cfg.socket_path)
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


def _wait_status(path: str, predicate, timeout: float = 5.0) -> dict:
    end = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < end:
        try:
            last = _read_json(path)
        except (OSError, ValueError):
            last = {}
        value = predicate(last)
        if value:
            return last
        time.sleep(0.02)
    raise AssertionError(f"status never satisfied predicate; last={last}")
