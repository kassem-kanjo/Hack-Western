"""Physical alarm: the Raspberry Pi 5 ACT LED follows the safety core's alarm state.

Solid on = the AI lookout sees a vessel that is not on AIS. Fast blink = LOOKOUT BLIND.
Off = watching. The LED is GPIO 9 on the BCM2712 "AON" block; QNX's ``gpio-aon-bcm`` utility
writes its pad registers (run through ``sudo -n``, which the image allows for the default user).

This process is separate from the safety core and only *reads* ``latest_decision.json``: a missing
utility, a missing sudo right or a crash here cannot touch the alarm path, and without the
hardware (macOS, a laptop) it logs once and exits 0. The ACT LED is active low on the Pi 5, so
"on" drives the pad low; ``--active-high`` flips that if a board turns out to differ.

    python -m darkfleet.edge.indicator --decision ~/darkfleet/latest_decision.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time

GPIO = "9"
STALE_S = 2.0  # a decision this old means the core is silent: treat as blind
BLINK_S = 0.2  # fast blink half-period
POLL_S = 0.05


def wanted(path: str, now: float | None = None) -> str:
    """'off' | 'on' | 'blink' for the current decision file (unreadable or stale = blink)."""
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as fh:
            decision = json.load(fh)
        if now - float(decision.get("t", 0.0)) > STALE_S:
            return "blink"
        level = (decision.get("alarm") or {}).get("level")
    except (OSError, ValueError, TypeError):
        return "blink"
    if level == "blind":
        return "blink"
    if level in ("warning", "critical"):
        return "on"
    return "off"


def _set(tool: str, lit: bool, active_low: bool) -> bool:
    level = "dl" if (lit == active_low) else "dh"
    try:
        result = subprocess.run(
            ["sudo", "-n", tool, "set", GPIO, "op", level],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfleet.edge.indicator")
    parser.add_argument("--decision", required=True)
    parser.add_argument("--tool", default="gpio-aon-bcm")
    parser.add_argument("--active-high", action="store_true", help="LED is lit by a high level")
    args = parser.parse_args(argv)
    active_low = not args.active_high

    if shutil.which(args.tool) is None:
        print(f"indicator: {args.tool} not found; no LED on this machine", file=sys.stderr, flush=True)
        return 0
    if not _set(args.tool, False, active_low):
        print("indicator: cannot drive the LED (no sudo right?); exiting", file=sys.stderr, flush=True)
        return 0
    print("indicator: ACT LED (GPIO 9) follows the alarm", file=sys.stderr, flush=True)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # the finally below turns the LED off
    lit = False
    blink_phase = False
    last_toggle = 0.0
    try:
        while True:
            mode = wanted(args.decision)
            now = time.monotonic()
            if mode == "blink":
                if now - last_toggle >= BLINK_S:
                    blink_phase = not blink_phase
                    last_toggle = now
                    if blink_phase != lit and _set(args.tool, blink_phase, active_low):
                        lit = blink_phase
            else:
                want = mode == "on"
                if want != lit and _set(args.tool, want, active_low):
                    lit = want
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        return 0
    finally:
        _set(args.tool, False, active_low)


if __name__ == "__main__":
    raise SystemExit(main())
