"""Supervisor: kill-the-detector recovery, and the safety core staying up while the detector is down.

These tests start real child processes (safety core and mock detector) on a short Unix socket
path under /tmp. They use no camera and no network. Every test stops the supervisor and checks
that its children are gone.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from darkfleet.edge import realtime
from darkfleet.edge.supervisor import Supervisor, SupervisorConfig, parse_qnxcam

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# A stand-in detector that never produces a frame: it only heartbeats "waiting for frames".
# This is the state a real detector is in while the bridge or camera is quiet.
_WAITING_DETECTOR = """
import socket, struct, sys, time
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(sys.argv[1])
body = (b'{"type":"HEARTBEAT","t":1.0,"state":"waiting for frames",'
        b'"sched":{"policy":"SCHED_OTHER","priority":0,"nice":10}}')
msg = struct.pack("!I", len(body)) + body
while True:
    sock.sendall(msg)
    time.sleep(0.15)
"""


class _WaitingSupervisor(Supervisor):
    """Launches the heartbeat-only stand-in instead of the real detector."""

    def _start_detector(self) -> None:
        self.detector.proc = subprocess.Popen(
            [sys.executable, "-c", _WAITING_DETECTOR, self.config.socket_path]
        )
        self.detector.started_wall = time.time()
        self._event("detector started", pid=self.detector.proc.pid)


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _wait(predicate, timeout: float, label: str):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {label}")


@pytest.fixture
def state_dir():
    path = tempfile.mkdtemp(prefix="dfe-", dir="/tmp")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _start(state_dir: str, **overrides) -> tuple[Supervisor, threading.Thread, threading.Event]:
    base = dict(
        source=FIXTURES,
        state_dir=state_dir,
        lat=55.0,
        lon=19.0,
        mock=True,
        fps=20.0,
        restart_backoff_s=0.5,
        set_priority=False,
    )
    base.update(overrides)
    sup = Supervisor(SupervisorConfig(**base))
    stop = threading.Event()
    thread = threading.Thread(target=sup.run, kwargs={"stop": stop, "duration_s": 60}, daemon=True)
    thread.start()
    return sup, thread, stop


def _stop(sup: Supervisor, thread: threading.Thread, stop: threading.Event) -> None:
    stop.set()
    thread.join(timeout=15)
    assert not thread.is_alive(), "supervisor did not stop"
    for child in (sup.detector, sup.camera, sup.safety):
        assert not _alive(None if child.proc is None else child.proc.pid), f"{child.name} left running"


def _edge(state_dir: str) -> dict:
    """The supervisor's status file; empty until its first write (the poll loop writes it)."""
    try:
        return _read(os.path.join(state_dir, "edge_status.json"))
    except (OSError, ValueError):
        return {}


def _wait_frames(state_dir: str, n: int) -> dict:
    return _wait(
        lambda: _edge(state_dir) if _edge(state_dir).get("frames_total", 0) >= n else None,
        timeout=10,
        label=f"{n} frames",
    )


def test_kill_the_detector_is_restarted_with_recovery_under_5s(state_dir):
    sup, thread, stop = _start(state_dir)
    try:
        status = _wait_frames(state_dir, 5)
        assert status["detector_alive"] is True
        old_pid = sup.detector_pid

        os.kill(old_pid, signal.SIGKILL)

        recovered = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("last_recovery_ms") is not None else None,
            timeout=10,
            label="recovery",
        )
        assert recovered["last_recovery_ms"] < 5000
        assert recovered["detector_restarts"] >= 1
        assert sup.detector_pid != old_pid
        assert recovered["detector_alive"] is True
        assert any(e["event"] == "detector recovered" for e in recovered["events"])
    finally:
        _stop(sup, thread, stop)


def test_safety_core_keeps_answering_status_while_detector_is_dead(state_dir):
    # A long back-off keeps the detector down for a window we can observe.
    sup, thread, stop = _start(state_dir, restart_backoff_s=2.0, standby=False)
    try:
        _wait_frames(state_dir, 5)
        os.kill(sup.detector_pid, signal.SIGKILL)

        safety_path = os.path.join(state_dir, "safety_status.json")
        seen_states: set[str] = set()
        stamps: list[float] = []
        end = time.monotonic() + 1.6
        while time.monotonic() < end:
            try:
                snap = _read(safety_path)
            except (OSError, ValueError):
                snap = None
            if snap is not None:
                seen_states.add(snap["state"])
                stamps.append(snap["t_wall"])
            time.sleep(0.05)

        assert "DEGRADED" in seen_states, seen_states
        assert len(set(stamps)) >= 5, "safety core stopped updating its status"
        assert stamps == sorted(stamps)
        # The supervisor agrees while the detector is down.
        assert _edge(state_dir)["state"] == "DEGRADED"

        recovered = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("last_recovery_ms") is not None else None,
            timeout=10,
            label="recovery after long outage",
        )
        assert recovered["last_recovery_ms"] >= 1500  # back-off was 2 s
        assert recovered["last_recovery_ms"] < 5000
    finally:
        _stop(sup, thread, stop)


def test_operator_kill_file_kills_and_restarts_the_detector(state_dir):
    sup, thread, stop = _start(state_dir)
    try:
        _wait_frames(state_dir, 5)
        old_pid = sup.detector_pid
        assert old_pid is not None

        control_dir = os.path.join(state_dir, "control")
        os.makedirs(control_dir, exist_ok=True)
        kill_file = os.path.join(control_dir, "kill_detector")
        with open(kill_file, "w", encoding="utf-8") as fh:
            fh.write("1")

        recovered = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("last_recovery_ms") is not None else None,
            timeout=10,
            label="operator kill recovery",
        )
        assert any(e["event"] == "detector killed by operator" for e in recovered["events"])
        assert recovered["detector_restarts"] >= 1
        assert recovered["detector_alive"] is True
        assert recovered["last_recovery_ms"] < 5000
        assert sup.detector_pid != old_pid
        assert not os.path.exists(kill_file)  # the supervisor consumes it exactly once
    finally:
        _stop(sup, thread, stop)


def test_mock_run_reports_alerts_and_writes_alert_log(state_dir):
    # Mock boats appear in 2 of every 3 frames, with no AIS cache: alerts must be raised.
    sup, thread, stop = _start(state_dir, cooldown_s=0.0)
    try:
        status = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("alerts_total", 0) >= 1 else None,
            timeout=10,
            label="first alert",
        )
        assert status["last_alert"]["kind"] == "VESSEL NOT BROADCASTING"
        alerts_path = os.path.join(state_dir, "alerts.jsonl")
        assert os.path.getsize(alerts_path) > 0
    finally:
        _stop(sup, thread, stop)


def test_waiting_detector_heartbeats_keep_it_alive(state_dir):
    """A quiet source is not a dead detector: 'waiting for frames' heartbeats must keep the
    supervisor from killing it, and no recovery_ms may be invented for the idle wait."""
    sup = _WaitingSupervisor(
        SupervisorConfig(
            source=FIXTURES,
            state_dir=state_dir,
            lat=55.0,
            lon=19.0,
            mock=True,
            heartbeat_timeout_s=0.5,  # far tighter than the 0.3 s heartbeat interval
            restart_backoff_s=0.5,
            set_priority=False,
        )
    )
    stop = threading.Event()
    thread = threading.Thread(target=sup.run, kwargs={"stop": stop, "duration_s": 60}, daemon=True)
    thread.start()
    try:
        seen = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("detector_waiting") else None,
            timeout=5,
            label="waiting detector heartbeat",
        )
        assert seen["detector_alive"] is True
        assert seen["detector_restarts"] == 0
        assert seen["last_recovery_ms"] is None
        assert not any(e["event"] == "detector silent, killing" for e in seen["events"])
        assert seen["state"] == "OK"  # heartbeats are liveness for the safety core too

        # The idle wait must not be counted as a restart/recovery either, later on.
        time.sleep(0.8)
        later = _edge(state_dir)
        assert later["detector_restarts"] == 0
        assert later["last_recovery_ms"] is None
    finally:
        _stop(sup, thread, stop)


def test_status_reports_every_process_priority(state_dir):
    sup, thread, stop = _start(state_dir)
    try:
        status = _wait_frames(state_dir, 3)
        priorities = status["priorities"]
        for name in ("safety_core", "supervisor", "detector"):
            assert set(priorities[name]) >= {"policy", "priority", "nice"}
            assert priorities[name]["policy"].startswith("SCHED_")
        # The detector runs at nice +10, whatever the OS allowed for the two trusted processes.
        assert priorities["detector"]["nice"] == 10
        assert priorities["detector"]["policy"] == "SCHED_OTHER"
        # No --qnxcam: the camera block is present and honestly reports no helper.
        assert status["camera"] == {"source": FIXTURES, "alive": False, "fps": 20.0}
        assert status["safety_tick"]["hz"] == 20
        assert set(status["safety_tick"]) == {
            "hz",
            "p50_late_ms",
            "p99_late_ms",
            "max_late_ms",
            "late_count_60s",
            "ticks_60s",
        }
    finally:
        _stop(sup, thread, stop)


def test_parse_qnxcam_spec() -> None:
    assert parse_qnxcam("5:/tmp/qnxcam.frame") == (5, "/tmp/qnxcam.frame")
    for bad in ("5", "five:/tmp/x", ":/tmp/x", "5:"):
        with pytest.raises(ValueError):
            parse_qnxcam(bad)


def test_qnxcam_helper_is_started_and_restarted(state_dir, tmp_path):
    """A camera helper that exits is restarted and both transitions are events."""
    fake = tmp_path / "fakecam"
    # Long enough that a loaded machine still observes the "alive" state between restarts.
    fake.write_text("#!/bin/sh\nsleep 1.5\n", encoding="utf-8")
    fake.chmod(0o755)
    sup = Supervisor(
        SupervisorConfig(
            source=FIXTURES,
            state_dir=state_dir,
            lat=55.0,
            lon=19.0,
            mock=True,
            fps=20.0,
            restart_backoff_s=0.2,
            set_priority=False,
            qnxcam=f"5:{tmp_path}/qnxcam.frame",
            qnxcam_bin=str(fake),
        )
    )
    stop = threading.Event()
    thread = threading.Thread(target=sup.run, kwargs={"stop": stop, "duration_s": 60}, daemon=True)
    thread.start()
    try:
        status = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("camera", {}).get("alive") else None,
            timeout=30,
            label="camera alive",
        )
        assert status["camera"]["source"] == f"5:{tmp_path}/qnxcam.frame"
        restarted = _wait(
            lambda: (
                _edge(state_dir)
                if {"camera stopped", "camera started"}
                <= {e["event"] for e in _edge(state_dir).get("events", [])}
                else None
            ),
            timeout=30,
            label="camera stopped and started again",
        )
        assert restarted["camera"]["source"] == f"5:{tmp_path}/qnxcam.frame"
    finally:
        _stop(sup, thread, stop)


def test_priority_fallback_is_reported_and_harmless(state_dir, monkeypatch):
    """macOS has no sched_setscheduler; the supervisor must log once and keep its normal class."""
    sup = Supervisor(
        SupervisorConfig(
            source=FIXTURES,
            state_dir=state_dir,
            lat=55.0,
            lon=19.0,
            mock=True,
            set_priority=True,
            fifo_priority=25,
        )
    )
    fallback = {"policy": "SCHED_OTHER", "priority": 0, "nice": 0}
    monkeypatch.setattr(realtime, "try_fifo", lambda _priority: (False, fallback))

    sup._apply_priority()

    assert sup._sched == fallback
    assert sup.status()["priorities"]["supervisor"] == fallback


def test_stress_control_file_starts_and_stops_workers(state_dir):
    sup, thread, stop = _start(state_dir, stress_workers=2)
    try:
        _wait_frames(state_dir, 3)
        path = os.path.join(state_dir, "control", "stress_ai")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1")

        active = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("stress", {}).get("active") else None,
            timeout=5,
            label="stress active",
        )
        assert active["stress"]["workers"] >= 1
        assert active["stress"]["until"] > time.time() - 1
        assert not os.path.exists(path), "the stress file is consumed exactly once"
        assert any(e["event"] == "stress started" for e in active["events"])

        idle = _wait(
            lambda: _edge(state_dir) if not _edge(state_dir).get("stress", {}).get("active") else None,
            timeout=10,
            label="stress ended",
        )
        assert idle["stress"]["workers"] == 0
        assert any(e["event"] == "stress ended" for e in idle["events"])
        assert sup._stress_procs == []
    finally:
        _stop(sup, thread, stop)


def test_warm_standby_is_promoted_instantly_and_replaced(state_dir):
    sup, thread, stop = _start(state_dir, restart_backoff_s=2.0)
    try:
        _wait_frames(state_dir, 5)
        _wait(lambda: _edge(state_dir).get("standby_alive") or None, timeout=10, label="standby up")
        old = sup.detector_pid
        os.kill(old, signal.SIGKILL)
        recovered = _wait(
            lambda: _edge(state_dir) if _edge(state_dir).get("last_recovery_ms") is not None else None,
            timeout=10,
            label="recovery through the standby",
        )
        # The 2 s cold back-off is bypassed: the standby already holds the model.
        assert recovered["last_recovery_ms"] < 1000
        assert sup.detector_pid != old
        _wait(lambda: _edge(state_dir).get("standby_alive") or None, timeout=10, label="new standby")
    finally:
        _stop(sup, thread, stop)
