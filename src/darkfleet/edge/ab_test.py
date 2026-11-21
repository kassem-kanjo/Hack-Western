"""A/B: the same alarm path under the same runaway load, with and without the priority guard.

QNX 8.0's adaptive partitioning is not on the Pi image (see docs/patrol-demo.md), so the guarantee
is fixed priorities: safety core SCHED_FIFO 30, supervisor FIFO 25, camera helper RR 15, AI
SCHED_RR 12, everything else (including a runaway load) at the default class (8). This script proves what
that buys. For each arm it starts a real supervisor on a ship photo (every frame is an alarm
frame: a vessel with no AIS ship near the patrol point), lets it settle, then runs four busy-loop
workers at the default priority for ``--stress-s`` seconds and reads the alarm path's numbers:

* alarms on time (frame timestamp to decision <= 200 ms) during the load,
* worst alarm latency and the safety loop's worst tick lateness.

Arm "unguarded" puts the core, the AI and the camera at the default class; arm "guarded" is the
normal unit. Result: ``<state-dir>/ab_result.json``, served as ``/api/edge/ab``. Run it on the Pi
with the normal unit stopped (``scripts/qnx_ab_run.sh`` does that and restarts the unit).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any


def _status(state_dir: str) -> dict[str, Any]:
    try:
        with open(os.path.join(state_dir, "edge_status.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _wait(state_dir: str, predicate, timeout: float) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = _status(state_dir)
        if predicate(status):
            return status
        time.sleep(0.2)
    raise SystemExit("A/B: the unit did not come up in time")


def run_arm(args: argparse.Namespace, guarded: bool) -> dict[str, Any]:
    name = "guarded" if guarded else "unguarded"
    state_dir = os.path.join(args.work_dir, name)
    shutil.rmtree(state_dir, ignore_errors=True)
    os.makedirs(os.path.join(state_dir, "control"), exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "darkfleet.edge.supervisor",
        "--source",
        f"dir:{args.frames}",
        "--model",
        args.model,
        "--input-size",
        str(args.input_size),
        "--state-dir",
        state_dir,
        "--lat",
        str(args.lat),
        "--lon",
        str(args.lon),
        "--ais",
        args.ais,
        "--fps",
        "10",
        "--no-standby",
    ]
    if not guarded:
        cmd.append("--no-guard")
    log = open(os.path.join(args.work_dir, f"{name}.log"), "w", encoding="utf-8")  # noqa: SIM115
    # Own process group: the whole arm (core, detector, camera-less hog workers) can be torn down.
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
    try:
        _wait(
            state_dir,
            lambda s: bool(s.get("detector_alive")) and (s.get("alarm") or {}).get("decisions", 0) >= 15,
            90.0,
        )
        time.sleep(3.0)
        base = _status(state_dir)
        while not base.get("alarm"):  # the status file is replaced atomically but can be mid-swap
            time.sleep(0.1)
            base = _status(state_dir)
        before = dict(base["alarm"])
        with open(os.path.join(state_dir, "control", "stress_ai"), "w", encoding="utf-8") as fh:
            fh.write(f"{args.stress_s:g}")
        peak_inference = 0.0
        end = time.monotonic() + args.stress_s + 2.0
        last: dict[str, Any] = {}
        while time.monotonic() < end:
            now_status = _status(state_dir)
            if now_status.get("alarm"):
                last = now_status
            peak_inference = max(peak_inference, float(last.get("last_inference_ms") or 0.0))
            time.sleep(0.25)
        alarm = last.get("alarm") or {}
        tick = last.get("safety_tick") or {}
        decisions = alarm.get("decisions", 0) - before.get("decisions", 0)
        on_time = alarm.get("on_time", 0) - before.get("on_time", 0)
        return {
            "alarm_frames": decisions,
            "on_time": on_time,
            "late": decisions - on_time,
            "worst_latency_ms": alarm.get("max_latency_ms"),
            "p99_latency_ms": alarm.get("p99_latency_ms"),
            "blind_episodes": alarm.get("blind_episodes", 0) - before.get("blind_episodes", 0),
            "worst_tick_late_ms": tick.get("max_late_ms"),
            "ticks_over_5ms": tick.get("late_count_60s"),
            "peak_inference_ms": round(peak_inference, 1),
            "priorities": last.get("priorities"),
            "debug": {
                "before": before.get("decisions"),
                "after": alarm.get("decisions"),
                "t_wall": time.time(),
            },
        }
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        # Whatever the supervisor left behind (a starved core, a detector) must not outlive the arm.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        log.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.ab_test")
    parser.add_argument("--frames", required=True, help="directory with one ship image")
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-size", type=int, default=320)
    parser.add_argument("--ais", required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True, help="ab_result.json to write")
    parser.add_argument("--stress-s", type=float, default=10.0)
    parser.add_argument("--arms", choices=("both", "guarded", "unguarded"), default="both")
    args = parser.parse_args(argv)

    result: dict[str, Any] = {
        "ran_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stress_s": args.stress_s,
        "deadline_ms": 200,
        "load": "4 busy-loop workers at the default priority",
    }
    for guarded in (False, True) if args.arms == "both" else (args.arms == "guarded",):
        name = "guarded" if guarded else "unguarded"
        print(f"A/B: running {name} arm", file=sys.stderr, flush=True)
        result[name] = run_arm(args, guarded)
        print(f"A/B: {name}: {json.dumps(result[name])}", file=sys.stderr, flush=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
    os.replace(tmp, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
