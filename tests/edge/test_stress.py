"""The stress worker: a bounded busy loop at the detector's priority, nothing more."""

from __future__ import annotations

import time

from darkfleet.edge import stress


def test_worker_returns_after_its_seconds() -> None:
    started = time.monotonic()

    assert stress.main(["--seconds", "0.1", "--nice", "0"]) == 0

    elapsed = time.monotonic() - started
    assert 0.05 <= elapsed < 2.0


def test_burn_holds_for_the_requested_time() -> None:
    started = time.monotonic()

    stress.burn(0.05)

    assert time.monotonic() - started >= 0.04


def test_worker_accepts_a_fractional_duration() -> None:
    assert stress.main(["--seconds", "0.01", "--nice", "0"]) == 0
