"""Busy-loop worker used by the supervisor to simulate the AI saturating every core.

The supervisor starts one of these per core at the detector's nice value (+10) for the
number of seconds in ``<state-dir>/control/stress_ai``. The loop is pure Python math on
purpose: it competes for the CPU without touching the camera, the model or the disk, and
it dies immediately on SIGTERM so the supervisor can reclaim the box.

This process never talks to the safety core or the detector socket.

Run: ``python -m darkfleet.edge.stress --seconds 10 --nice 10``
"""

from __future__ import annotations

import argparse
import math
import os
import time

from darkfleet.edge import realtime


def burn(seconds: float) -> None:
    """Hold one core for ``seconds``; no allocation, no I/O, no syscalls in the loop."""
    end = time.monotonic() + seconds
    value = 0.0
    while time.monotonic() < end:
        for step in range(4096):
            value = math.sqrt(value + step + 1.0)
        if value > 1e6:  # keep the float finite across long runs
            value = 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.stress")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--nice", type=int, default=10)
    args = parser.parse_args(argv)

    # Drop any inherited SCHED_FIFO first: this worker must match the detector's class.
    realtime.drop_realtime()
    if args.nice > 0:
        try:
            os.nice(args.nice)
        except OSError:
            pass
    burn(max(0.0, args.seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
