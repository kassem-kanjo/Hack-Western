"""Supervisor: starts the safety core and the detector, watches them, reports status.

* Starts the safety core first (high priority, trusted), then the detector (low priority, untrusted).
* Restarts the detector when it exits, or when it goes silent for `heartbeat_timeout_s`
  (a hung process is killed first).
* Measures `recovery_ms`: from the moment the supervisor sees the detector die to the first
  DETECTIONS that arrives afterwards. This is the number the demo quotes.
* Writes `edge_status.json` for the console (state, last alert, deadline misses, last recovery).

The supervisor never runs inference and never touches the alert logic. It only starts, stops,
and watches processes. The safety core keeps running while the detector is down, and reports
DEGRADED through its own status file.

Run: `python -m darkfleet.edge.supervisor --source tests/edge/fixtures --mock`
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import realtime

_POLL_S = 0.1
_STARTUP_WAIT_S = 5.0
_STOP_GRACE_S = 2.0
_EVENTS_KEEP = 20
_CONTROL_DIR = "control"
_KILL_FILE = "kill_detector"
_STRESS_FILE = "stress_ai"
_STRESS_MAX_S = 120.0
_STRESS_MIN_S = 0.1


@dataclass(frozen=True)
class SupervisorConfig:
    source: str
    state_dir: str
    lat: float
    lon: float
    mock: bool = False
    model: str | None = None
    ais: str | None = None
    fps: float = 10.0
    match_km: float = 2.0
    deadline_ms: float = 200.0
    cooldown_s: float = 5.0
    heartbeat_timeout_s: float = 2.0
    # Loading the model takes longer than heartbeat_timeout_s on a Raspberry Pi (OpenCV DNN), and
    # the detector sends nothing until it is ready; without a grace it was killed mid-load forever.
    startup_grace_s: float = 20.0
    restart_backoff_s: float = 0.5
    set_priority: bool = True
    fifo_priority: int = 25  # the supervisor sits below the safety core (30)
    detector_nice: int = 10  # the detector's ordinary-class priority when it is not guarded
    # Priority guard (QNX fixed-priority scheduling; this image has no adaptive partitioning):
    # safety core FIFO 30 > supervisor FIFO 25 > camera helper RR 15 > AI RR 12 > everything else (8).
    # The AI must stay BELOW the camera helper: at RR 20 its spinning OpenCV threads starved the
    # camera service on the Pi and the stream stopped after 3 frames (measured).
    # guard=False puts the core, the AI and the camera at the default class: the A/B baseline.
    guard: bool = True
    detector_rt: int = 12
    camera_rt: int = 15
    stress_nice: int = 0  # the "runaway load" runs at the ordinary default priority
    act_led: bool = False  # drive the Pi 5 ACT LED from the decision file
    stress_workers: int | None = None  # default: one per core
    stress_seconds: float = 10.0  # default when the stress file is empty
    # QNX webcam: "<unit>:<path>" spawns the qnxcam helper at the detector's priority.
    # Warm standby: a second detector with the model already loaded, promoted the instant the
    # active one dies (loading the model is most of a cold restart on the Pi).
    standby: bool = True
    input_size: int = 416
    qnxcam: str | None = None
    qnxcam_bin: str = "~/darkfleet/qnxcam"
    patrol_name: str = "patrol"

    @property
    def socket_path(self) -> str:
        return os.path.join(self.state_dir, "edge.sock")

    @property
    def safety_status_path(self) -> str:
        return os.path.join(self.state_dir, "safety_status.json")

    @property
    def edge_status_path(self) -> str:
        return os.path.join(self.state_dir, "edge_status.json")

    @property
    def alerts_path(self) -> str:
        return os.path.join(self.state_dir, "alerts.jsonl")

    @property
    def decision_path(self) -> str:
        return os.path.join(self.state_dir, "latest_decision.json")

    @property
    def kill_signal_path(self) -> str:
        return os.path.join(self.state_dir, _CONTROL_DIR, _KILL_FILE)

    @property
    def stress_signal_path(self) -> str:
        return os.path.join(self.state_dir, _CONTROL_DIR, _STRESS_FILE)


@dataclass
class _Child:
    name: str
    proc: subprocess.Popen | None = None
    started_wall: float = 0.0
    restarts: int = 0


@dataclass
class _Stats:
    detector_restarts: int = 0
    last_recovery_ms: float | None = None
    recovery_ms_history: list[float] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class Supervisor:
    def __init__(self, config: SupervisorConfig) -> None:
        self.config = config
        self.safety = _Child("safety_core")
        self.detector = _Child("detector")
        self.standby = _Child("standby")
        self.indicator = _Child("indicator")
        self._standby_retry_at = 0.0
        self.camera = _Child("qnxcam")
        self.stats = _Stats()
        self._death_wall: float | None = None  # when we noticed the detector was gone
        self._detector_restart_at: float | None = None  # monotonic time of the next detector start
        self._safety_restart_at: float | None = None
        self._camera_restart_at: float | None = None
        self._started_wall = time.time()
        self._sched: dict[str, Any] = realtime.current_schedule()
        self._stress_procs: list[subprocess.Popen] = []
        self._stress_until: float | None = None  # wall clock
        os.makedirs(config.state_dir, exist_ok=True)

    # --- public -------------------------------------------------------------

    @property
    def detector_pid(self) -> int | None:
        return None if self.detector.proc is None else self.detector.proc.pid

    def run(self, stop: threading.Event | None = None, duration_s: float | None = None) -> None:
        self._apply_priority()
        start = time.monotonic()
        try:
            self._start_safety_core()
            self._wait_for_socket()
            self._start_camera()
            self._start_detector()
            self._start_indicator()
            while stop is None or not stop.is_set():
                if duration_s is not None and time.monotonic() - start >= duration_s:
                    break
                time.sleep(_POLL_S)
                self._watch()
                self._write_edge_status()
        finally:
            self._stop_children()
            self._write_edge_status()

    def _apply_priority(self) -> None:
        """SCHED_FIFO 25 when the OS allows it; otherwise one log line and carry on."""
        if not self.config.set_priority:
            self._sched = realtime.current_schedule()
            print(
                f"supervisor priority: not requested ({realtime.describe(self._sched)})",
                file=sys.stderr,
                flush=True,
            )
            return
        elevated, sched = realtime.try_fifo(self.config.fifo_priority)
        self._sched = sched
        if elevated:
            print(f"supervisor priority: {realtime.describe(sched)}", file=sys.stderr, flush=True)
        else:
            print(
                f"supervisor: SCHED_FIFO {self.config.fifo_priority} unavailable "
                f"({realtime.describe(sched)}); continuing at normal priority",
                file=sys.stderr,
                flush=True,
            )

    # --- children -----------------------------------------------------------

    def _start_safety_core(self) -> None:
        cfg = self.config
        cmd = [
            sys.executable,
            "-m",
            "darkfleet.edge.safety_core",
            "--socket",
            cfg.socket_path,
            "--status",
            cfg.safety_status_path,
            "--alerts",
            cfg.alerts_path,
            "--lat",
            str(cfg.lat),
            "--lon",
            str(cfg.lon),
            "--match-km",
            str(cfg.match_km),
            "--deadline-ms",
            str(cfg.deadline_ms),
            "--cooldown-s",
            str(cfg.cooldown_s),
            "--decision",
            cfg.decision_path,
            "--patrol-name",
            cfg.patrol_name,
            "--source",
            self._source_label(),
        ]
        if cfg.ais:
            cmd += ["--ais", cfg.ais]
        if not cfg.set_priority or not cfg.guard:
            cmd += ["--no-priority"]
        # A socket file left by a killed run would look "ready" to _wait_for_socket. Remove it first.
        if os.path.exists(cfg.socket_path) and stat.S_ISSOCK(os.stat(cfg.socket_path).st_mode):
            os.unlink(cfg.socket_path)
        # Unguarded baseline: give back the supervisor's SCHED_FIFO so the core is just another process.
        self.safety.proc = subprocess.Popen(cmd, preexec_fn=None if cfg.guard else _low_priority_preexec(0))
        self.safety.started_wall = time.time()
        self._event("safety_core started", pid=self.safety.proc.pid)

    def _detector_cmd(self, standby: bool) -> list[str]:
        cfg = self.config
        cmd = [
            sys.executable,
            "-m",
            "darkfleet.edge.detector",
            "--source",
            cfg.source,
            "--socket",
            cfg.socket_path,
            "--state-dir",
            cfg.state_dir,
            "--fps",
            str(cfg.fps),
        ]
        if cfg.mock:
            cmd += ["--mock"]
        if cfg.model:
            cmd += ["--model", cfg.model]
        cmd += ["--nice", str(cfg.detector_nice), "--input-size", str(cfg.input_size)]
        if cfg.guard and cfg.detector_rt > 0:
            cmd += ["--rt-priority", str(cfg.detector_rt)]
        if standby:
            cmd += ["--standby"]
        return cmd

    def _start_detector(self) -> None:
        self.detector.proc = subprocess.Popen(self._detector_cmd(standby=False))
        self.detector.started_wall = time.time()
        self._event("detector started", pid=self.detector.proc.pid)

    def _start_standby(self) -> None:
        self.standby.proc = subprocess.Popen(self._detector_cmd(standby=True))
        self.standby.started_wall = time.time()
        self._event("standby detector loading", pid=self.standby.proc.pid)

    def _promote_standby(self) -> bool:
        """Make the warm standby the active detector (SIGUSR1). False if there is none."""
        proc = self.standby.proc
        if proc is None or proc.poll() is not None:
            self.standby.proc = None
            return False
        proc.send_signal(signal.SIGUSR1)
        try:
            os.unlink(os.path.join(self.config.state_dir, f"standby_{proc.pid}.ready"))
        except OSError:
            pass
        self.detector.proc = proc
        self.detector.started_wall = time.time()
        self.standby.proc = None
        self._event("standby promoted", pid=proc.pid)
        return True

    def _start_indicator(self) -> None:
        """The ACT LED follows the decision file from its own low-priority process."""
        if not self.config.act_led:
            return
        cmd = [
            sys.executable,
            "-m",
            "darkfleet.edge.indicator",
            "--decision",
            self.config.decision_path,
        ]
        self.indicator.proc = subprocess.Popen(
            cmd, preexec_fn=_low_priority_preexec(self.config.detector_nice)
        )
        self._event("indicator started", pid=self.indicator.proc.pid)

    def _standby_ready(self) -> bool:
        """True once the standby has loaded and warmed the model (it writes a marker file)."""
        proc = self.standby.proc
        if proc is None or proc.poll() is not None:
            return False
        return os.path.exists(os.path.join(self.config.state_dir, f"standby_{proc.pid}.ready"))

    def _keep_standby(self, now: float) -> None:
        """Start the next standby once the active detector has produced frames."""
        if not self.config.standby or self.standby.proc is not None:
            return
        if self.standby.proc is None and now < self._standby_retry_at:
            return
        status = self._read_safety_status()
        if not status or not status.get("frames_total"):
            return  # let the active detector load first: two model loads at once slow both
        self._standby_retry_at = now + 2.0
        self._start_standby()

    def _source_label(self) -> str:
        """What the decision file calls this source: ``qnxcam:5`` for the webcam, else the spec."""
        if self.config.qnxcam:
            try:
                unit, _path = parse_qnxcam(self.config.qnxcam)
            except ValueError:
                return self.config.source
            return f"qnxcam:{unit}"
        return self.config.source

    def _start_camera(self) -> None:
        """Spawn the QNX camera helper at the detector's low priority and let it restart itself.

        qnxcam is untrusted input like the detector: it gives up any inherited SCHED_FIFO and
        takes the detector's nice value in a pre-exec hook, exactly as the detector does.
        """
        cfg = self.config
        if not cfg.qnxcam:
            return
        unit, path = parse_qnxcam(cfg.qnxcam)
        binary = os.path.expanduser(cfg.qnxcam_bin)
        cmd = [binary, str(unit), path, f"{cfg.fps:g}"]
        try:
            self.camera.proc = subprocess.Popen(
                cmd,
                preexec_fn=_low_priority_preexec(cfg.detector_nice, cfg.camera_rt if cfg.guard else 0),
            )
        except OSError as exc:
            self.camera.proc = None
            self._camera_restart_at = time.monotonic() + cfg.restart_backoff_s
            self._event("camera failed", error=str(exc))
            return
        self.camera.started_wall = time.time()
        self._event("camera started", pid=self.camera.proc.pid, source=self._source_label())

    def _wait_for_socket(self) -> None:
        deadline = time.monotonic() + _STARTUP_WAIT_S
        while not os.path.exists(self.config.socket_path):
            if time.monotonic() > deadline:
                raise RuntimeError("safety core did not create its socket in time")
            if self.safety.proc is not None and self.safety.proc.poll() is not None:
                raise RuntimeError(f"safety core exited at start (code {self.safety.proc.returncode})")
            time.sleep(0.02)

    def _watch(self) -> None:
        """One watchdog turn. Restarts are scheduled, not slept, so status keeps updating."""
        cfg = self.config
        now = time.monotonic()
        now_wall = time.time()

        self._check_operator_kill()
        self._check_stress_request()
        self._reap_stress(now_wall)

        # The safety core is trusted and simple; if it dies, bring it back and say so.
        if self.safety.proc is not None and self.safety.proc.poll() is not None:
            self._event("safety_core exited", code=self.safety.proc.returncode)
            self._safety_restart_at = now + self.config.restart_backoff_s
            self.safety.proc = None
        if (
            self.safety.proc is None
            and self._safety_restart_at is not None
            and now >= self._safety_restart_at
        ):
            self._safety_restart_at = None
            self._start_safety_core()

        # The camera helper is restartable on its own: qnxcam exits when the USB device drops.
        if cfg.qnxcam:
            if self.camera.proc is not None and self.camera.proc.poll() is not None:
                self._event("camera stopped", code=self.camera.proc.returncode)
                self.camera.proc = None
                self.camera.restarts += 1
                self._camera_restart_at = now + cfg.restart_backoff_s
            if self.camera.proc is None and (
                self._camera_restart_at is None or now >= self._camera_restart_at
            ):
                self._camera_restart_at = None
                self._start_camera()

        if self.standby.proc is not None and self.standby.proc.poll() is not None:
            self._event("standby exited", code=self.standby.proc.returncode)
            self.standby.proc = None

        if self.detector.proc is None:
            if self._detector_restart_at is not None and now >= self._detector_restart_at:
                self._detector_restart_at = None
                self.stats.detector_restarts += 1
                self.detector.restarts += 1
                self._start_detector()
            return

        exited = self.detector.proc.poll() is not None
        if not exited and self._silent_for(now_wall) > self.config.heartbeat_timeout_s:
            self._event("detector silent, killing", silent_s=round(self._silent_for(now_wall), 2))
            self.detector.proc.kill()
            self.detector.proc.wait()
            exited = True

        if exited:
            if self._death_wall is None:
                self._death_wall = time.time()
                self._event("detector down", code=self.detector.proc.returncode)
            self.detector.proc = None
            if self._promote_standby():
                self.stats.detector_restarts += 1
                self.detector.restarts += 1
                return
            self._detector_restart_at = now + self.config.restart_backoff_s
            return

        self._keep_standby(now)

        # Recovery: the first DETECTIONS that arrives after the death.
        if self._death_wall is not None:
            status = self._read_safety_status()
            frame_wall = status.get("last_frame_wall") if status else None
            if frame_wall is not None and frame_wall > self._death_wall:
                recovery_ms = (frame_wall - self._death_wall) * 1000.0
                self.stats.last_recovery_ms = round(recovery_ms, 1)
                self.stats.recovery_ms_history.append(self.stats.last_recovery_ms)
                self._event("detector recovered", recovery_ms=self.stats.last_recovery_ms)
                self._death_wall = None

    def _check_operator_kill(self) -> None:
        """Consume the operator's kill file; the normal restart + recovery path then applies."""
        path = self.config.kill_signal_path
        try:
            os.unlink(path)
        except FileNotFoundError:
            return
        except OSError:
            return
        proc = self.detector.proc
        if proc is None or proc.poll() is not None:
            return
        # The clock starts at the kill, not when the next watchdog turn notices it.
        if self._death_wall is None:
            self._death_wall = time.time()
        proc.kill()
        proc.wait()
        self._event("detector killed by operator")

    def _check_stress_request(self) -> None:
        """Consume the operator's stress file: its contents are the number of seconds."""
        path = self.config.stress_signal_path
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            return
        try:
            os.unlink(path)
        except OSError:
            return
        self._start_stress(_stress_seconds(raw, self.config.stress_seconds))

    def _start_stress(self, seconds: float) -> None:
        """Start one busy-loop worker per core at the detector's nice value."""
        self._stop_stress()
        workers = self.config.stress_workers or os.cpu_count() or 1
        cmd = [
            sys.executable,
            "-m",
            "darkfleet.edge.stress",
            "--seconds",
            f"{seconds:.3f}",
            "--nice",
            str(self.config.stress_nice),
        ]
        for _ in range(max(1, int(workers))):
            self._stress_procs.append(subprocess.Popen(cmd))
        self._stress_until = time.time() + seconds
        self._event("stress started", workers=int(workers), seconds=seconds)

    def _reap_stress(self, now_wall: float) -> None:
        """Stop the workers once their time is up (or one of them exits early)."""
        if not self._stress_procs:
            return
        expired = self._stress_until is not None and now_wall >= self._stress_until
        if expired or any(proc.poll() is not None for proc in self._stress_procs):
            self._stop_stress()
            self._event("stress ended")

    def _stop_stress(self) -> None:
        procs, self._stress_procs = self._stress_procs, []
        self._stress_until = None
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=_STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _silent_for(self, now_wall: float) -> float:
        """Seconds since the detector last sent anything, counting from its own start."""
        status = self._read_safety_status()
        last = status.get("last_msg_wall") if status else None
        if last is None or last < self.detector.started_wall:
            # Not heard from since this start: allow the model to load before calling it silent.
            return max(0.0, now_wall - self.detector.started_wall - self.config.startup_grace_s)
        return now_wall - last

    def _stop_children(self) -> None:
        self._stop_stress()
        for child in (self.indicator, self.detector, self.standby, self.camera, self.safety):
            proc = child.proc
            if proc is None or proc.poll() is not None:
                continue
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=_STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    # --- status -------------------------------------------------------------

    def _read_safety_status(self) -> dict[str, Any] | None:
        try:
            with open(self.config.safety_status_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _event(self, text: str, **extra: Any) -> None:
        record = {"t_wall": time.time(), "event": text, **extra}
        self.stats.events = (self.stats.events + [record])[-_EVENTS_KEEP:]
        print(f"[supervisor] {text} {extra if extra else ''}".rstrip(), file=sys.stderr, flush=True)

    def status(self) -> dict[str, Any]:
        safety = self._read_safety_status() or {}
        detector_alive = self.detector.proc is not None and self.detector.proc.poll() is None
        safety_alive = self.safety.proc is not None and self.safety.proc.poll() is None
        state = safety.get("state", "UNKNOWN")
        if not detector_alive and state == "OK":
            state = "DEGRADED"  # Safety core may not have noticed yet; the supervisor knows.
        now_wall = time.time()
        stress_workers = sum(1 for proc in self._stress_procs if proc.poll() is None)
        camera_alive = self.camera.proc is not None and self.camera.proc.poll() is None
        return {
            "component": "supervisor",
            "t_wall": now_wall,
            "started_wall": self._started_wall,
            "mode": "mock" if self.config.mock else "camera",
            "state": state,
            "state_reason": safety.get("state_reason"),
            "priority": safety.get("priority"),
            "priorities": self._priorities(safety),
            "last_alert": safety.get("last_alert"),
            "deadline_ms": safety.get("deadline_ms"),
            "deadline_misses": safety.get("deadline_misses", 0),
            "deadline_misses_60s": safety.get("deadline_misses_60s", 0),
            "frames_total": safety.get("frames_total", 0),
            "frames_per_s": safety.get("frames_per_s", 0.0),
            "latency_p50_ms": safety.get("latency_p50_ms"),
            "latency_p99_ms": safety.get("latency_p99_ms"),
            "alerts_total": safety.get("alerts_total", 0),
            "last_frame_age_ms": safety.get("last_frame_age_ms"),
            "last_inference_ms": safety.get("last_inference_ms"),
            "safety_tick": safety.get("safety_tick"),
            "alarm": safety.get("alarm"),
            "detector_waiting": safety.get("detector_waiting", False),
            "last_recovery_ms": self.stats.last_recovery_ms,
            "recovery_ms_history": self.stats.recovery_ms_history[-20:],
            "detector_restarts": self.stats.detector_restarts,
            "detector_pid": self.detector_pid,
            "detector_alive": detector_alive,
            "standby_alive": self._standby_ready(),
            "safety_core_pid": None if self.safety.proc is None else self.safety.proc.pid,
            "safety_core_alive": safety_alive,
            "camera": {
                "source": self.config.qnxcam or self.config.source,
                "alive": camera_alive,
                "fps": self.config.fps,
            },
            "stress": {
                "active": self._stress_until is not None and now_wall < self._stress_until,
                "until": self._stress_until,
                "workers": stress_workers,
            },
            "events": self.stats.events,
        }

    def _priorities(self, safety: dict[str, Any]) -> dict[str, Any]:
        """The scheduling class each process actually has (detector: as it reported)."""
        detector = safety.get("detector_sched")
        if not isinstance(detector, dict) or not detector.get("policy"):
            detector = {
                "policy": realtime.SCHED_OTHER,
                "priority": 0,
                "nice": self.config.detector_nice,
            }
        return {
            "safety_core": safety.get("sched") or {},
            "supervisor": self._sched,
            "detector": detector,
        }

    def _write_edge_status(self) -> None:
        obj = self.status()
        tmp = self.config.edge_status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True)
        os.replace(tmp, self.config.edge_status_path)


def _stress_seconds(raw: str, default: float) -> float:
    """The stress file holds a number of seconds; anything else means the default."""
    try:
        seconds = float(raw) if raw else default
    except ValueError:
        seconds = default
    return min(max(seconds, _STRESS_MIN_S), _STRESS_MAX_S)


def parse_qnxcam(spec: str) -> tuple[int, str]:
    """Parse ``--qnxcam "<unit>:<path>"`` into the camera unit and the frame file."""
    unit_text, sep, path = spec.strip().partition(":")
    if not sep or not unit_text.isdigit() or not path:
        raise ValueError(f"--qnxcam needs '<unit>:<path>', got {spec!r}")
    return int(unit_text), path


def _low_priority_preexec(nice: int, rt: int = 0):
    """Give the camera helper back any inherited SCHED_FIFO, then lower its nice value.

    With ``rt`` it takes SCHED_RR at that priority instead: the camera is an input the lookout
    cannot do without, so a runaway load must not starve it (stale frames would raise BLIND).
    """

    def _preexec() -> None:
        realtime.drop_realtime()
        try:
            if rt > 0:
                realtime.try_rr(rt)
            elif nice > 0:
                os.nice(nice)
        except OSError:
            pass

    return _preexec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edge unit supervisor: safety core + detector.")
    parser.add_argument(
        "--source",
        required=True,
        help="dir:<path>, camera:<index> or net:<port> (plain path = directory)",
    )
    parser.add_argument("--mock", action="store_true", help="synthetic detections (no model, no camera)")
    parser.add_argument("--model", default=None, help="ONNX model path (real mode)")
    parser.add_argument("--state-dir", default="data/edge", help="where the socket, status and alerts go")
    # Placeholder unit position (Baltic). Set the real one for a deployed unit.
    parser.add_argument("--lat", type=float, default=55.2)
    parser.add_argument("--lon", type=float, default=19.9)
    parser.add_argument("--ais", default=None, help="AIS snapshot JSON (list of {mmsi, lat, lon, t})")
    parser.add_argument("--patrol-name", default="patrol", help="name shown in latest_decision.json")
    parser.add_argument("--qnxcam", default=None, help='spawn qnxcam for "<unit>:<path>" frame file')
    parser.add_argument(
        "--qnxcam-bin", default="~/darkfleet/qnxcam", help="qnxcam binary (built by edge_deploy.sh)"
    )
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--input-size", type=int, default=416, help="detector network input (320 on the Pi)")
    parser.add_argument(
        "--no-guard",
        action="store_true",
        help="A/B baseline: safety core, AI and camera at the default class (no priority guard)",
    )
    parser.add_argument("--act-led", action="store_true", help="drive the Pi 5 ACT LED from the alarm state")
    parser.add_argument("--no-standby", action="store_true", help="cold-restart the detector instead")
    parser.add_argument("--match-km", type=float, default=2.0)
    parser.add_argument("--deadline-ms", type=float, default=200.0)
    parser.add_argument("--fifo-priority", type=int, default=25, help="SCHED_FIFO priority to ask for")
    parser.add_argument("--stress-seconds", type=float, default=10.0, help="default stress duration")
    parser.add_argument("--no-priority", action="store_true")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    args = parser.parse_args(argv)

    config = SupervisorConfig(
        source=args.source,
        state_dir=os.path.abspath(args.state_dir),
        lat=args.lat,
        lon=args.lon,
        mock=args.mock,
        model=args.model,
        ais=os.path.abspath(args.ais) if args.ais else None,
        fps=args.fps,
        input_size=args.input_size,
        standby=not args.no_standby,
        guard=not args.no_guard,
        detector_nice=0 if args.no_guard else 10,
        act_led=args.act_led,
        match_km=args.match_km,
        deadline_ms=args.deadline_ms,
        set_priority=not args.no_priority,
        fifo_priority=args.fifo_priority,
        stress_seconds=args.stress_seconds,
        qnxcam=args.qnxcam,
        qnxcam_bin=args.qnxcam_bin,
        patrol_name=args.patrol_name,
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    sup = Supervisor(config)
    try:
        sup.run(stop=stop, duration_s=args.duration)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
