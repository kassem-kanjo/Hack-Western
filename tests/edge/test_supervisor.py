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
import tempfile
import threading
import time

import pytest

from darkfleet.edge.supervisor import Supervisor, SupervisorConfig

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


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
    for child in (sup.detector, sup.safety):
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
    sup, thread, stop = _start(state_dir, restart_backoff_s=2.0)
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
