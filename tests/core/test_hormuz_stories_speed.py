"""Speed and cap summary of an outage cluster (#219): the implied speed the reported positions need."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.hormuz_stories import Gap, cluster_summary

T0 = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)


def _fast(i: int, implied: float | None, *, class_cap: float = 16.1) -> Gap:
    return Gap(
        id=f"f{i}",
        vessel_key=f"f{i}",
        start=T0 + timedelta(minutes=i),
        end=T0 + timedelta(hours=20, minutes=i),
        off_lat=19.5,
        off_lon=62.6 + i * 0.5,
        on_lat=25.22,
        on_lon=54.58,
        implied_kn=implied,
        class_cap_kn=class_cap,
    )


def test_summary_reports_speed_and_raised_caps() -> None:
    speeds = [23.0, 30.0, 40.0, 35.0, 28.0, 31.0, 32.0, 29.0]
    summary = cluster_summary([_fast(i, s) for i, s in enumerate(speeds)])
    assert summary["median_kn"] == 30.5
    assert summary["min_kn"] == 23.0
    assert summary["raised_cap"] == 8  # every implied speed is above a 16.1 kn class cap
    assert summary["median_hours"] == 20.0
    assert summary["max_km"] > 0


def test_summary_without_speeds_says_so() -> None:
    summary = cluster_summary([_fast(i, None) for i in range(8)])
    assert summary["median_kn"] is None
    assert summary["min_kn"] is None
    assert summary["raised_cap"] == 0


@settings(max_examples=100, deadline=None)
@given(st.lists(st.floats(0.5, 60.0), min_size=1, max_size=20))
def test_speed_summary_is_ordered(speeds: list[float]) -> None:
    summary = cluster_summary([_fast(i, s) for i, s in enumerate(speeds)])
    assert summary["min_kn"] <= summary["median_kn"] + 1e-9
    assert summary["raised_cap"] <= summary["vessels"]
