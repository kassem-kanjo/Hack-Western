"""Tests for the pure event-study helpers.

The deterministic cases pin down the arithmetic and the window edges; the Hypothesis
properties cover the promises the report relies on: identical areas give a zero DiD, a
bootstrap interval contains its point estimate, and thin days are flagged rather than
compared.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.event_study import (
    VERDICT_INSUFFICIENT,
    VERDICT_MOVES,
    VERDICT_NONE,
    event_study,
    monthly_coverage,
    regime_comparison,
)

START = date(2025, 1, 1)
EVENTS = [{"date_start": "2025-01-31", "date_end": "2025-02-02", "title": "test event", "type": "test"}]


def _series(rows: list[tuple[int, int]], start: date = START) -> list[dict]:
    return [
        {"day": (start + timedelta(days=index)).isoformat(), "seen": seen, "dark": dark}
        for index, (seen, dark) in enumerate(rows)
    ]


def _flat(days: int, seen: int, dark: int) -> list[tuple[int, int]]:
    return [(seen, dark)] * days


@st.composite
def daily_series(draw, n_days: int = 80) -> list[dict]:
    """A well-covered 80-day series with arbitrary (valid) dark counts."""
    pairs = draw(
        st.lists(
            st.tuples(st.integers(min_value=20, max_value=300), st.integers(min_value=0, max_value=300)),
            min_size=n_days,
            max_size=n_days,
        )
    )
    rows = [(seen, raw_dark % (seen + 1)) for seen, raw_dark in pairs]
    return _series(rows)


def test_did_subtracts_the_control_change() -> None:
    treated = _series(_flat(20, 100, 10) + [(100, 40)] * 20)
    control = _series(_flat(40, 100, 20))
    events = [{"date_start": "2025-01-19", "date_end": "2025-01-21", "title": "known"}]

    result = event_study(treated, control, events, window_days=5, n_boot=200, seed=1)[0]

    assert result["treated"]["pre"]["dark_share"] == pytest.approx(0.10)
    assert result["treated"]["post"]["dark_share"] == pytest.approx(0.40)
    assert result["control"]["pre"]["dark_share"] == pytest.approx(0.20)
    assert result["control"]["post"]["dark_share"] == pytest.approx(0.20)
    assert result["did"] == pytest.approx(0.30)
    assert result["verdict"] == VERDICT_MOVES


def test_window_is_exactly_the_days_around_the_event() -> None:
    rows = [(100, day) for day in range(1, 32)]
    series = _series(rows, start=date(2025, 1, 1))
    event = {"date_start": "2025-01-15", "date_end": "2025-01-16", "title": "edges"}

    result = event_study(series, series, [event], window_days=2, n_boot=50)[0]

    # Pre is Jan 13-14 and post is Jan 17-18; the event days themselves never enter.
    assert result["treated"]["pre"]["seen"] == 200
    assert result["treated"]["pre"]["dark"] == 13 + 14
    assert result["treated"]["post"]["dark"] == 17 + 18
    assert result["treated"]["pre"]["days"] == 2
    assert result["treated"]["post"]["days"] == 2


def test_identical_areas_give_a_zero_did_and_no_detectable_change() -> None:
    series = _series(_flat(60, 100, 30))
    result = event_study(series, list(series), EVENTS, window_days=10, n_boot=500, seed=3)[0]

    assert result["did"] == pytest.approx(0.0)
    assert result["did_ci"][0] <= 0.0 <= result["did_ci"][1]
    assert result["verdict"] == VERDICT_NONE


def test_event_study_is_reproducible_for_a_seed() -> None:
    treated = _series([(100, index % 70) for index in range(60)])
    control = _series([(100, (index * 3) % 40) for index in range(60)])

    first = event_study(treated, control, EVENTS, n_boot=300, seed=7)
    second = event_study(treated, control, EVENTS, n_boot=300, seed=7)
    other = event_study(treated, control, EVENTS, n_boot=300, seed=8)

    assert first == second
    assert first[0]["did_ci"] != other[0]["did_ci"]


def test_thin_days_are_insufficient_coverage() -> None:
    thin = _series([(5, 1)] * 60)
    result = event_study(thin, thin, EVENTS, min_seen=20, n_boot=100)[0]

    assert result["verdict"] == VERDICT_INSUFFICIENT
    assert result["did"] is None
    assert result["did_ci"] is None
    assert result["treated"]["pre"]["days"] == 0
    assert result["treated"]["post"]["days"] == 0


def test_missing_event_window_is_insufficient_coverage() -> None:
    # The series ends before the event, so the post window has no rows at all.
    series = _series(_flat(10, 100, 10))
    event = {"date_start": "2025-03-01", "date_end": "2025-03-02", "title": "later"}

    result = event_study(series, series, [event], n_boot=100)[0]

    assert result["verdict"] == VERDICT_INSUFFICIENT
    assert result["control"]["post"]["days"] == 0


def test_regime_comparison_uses_two_ranges() -> None:
    treated = _series(_flat(10, 100, 10) + _flat(10, 100, 40))
    control = _series(_flat(20, 100, 20))

    regime = regime_comparison(
        treated,
        control,
        ("2025-01-01", "2025-01-10"),
        ("2025-01-11", "2025-01-20"),
        n_boot=200,
        seed=2,
    )

    assert regime["treated"]["change"] == pytest.approx(0.30)
    assert regime["control"]["change"] == pytest.approx(0.0)
    assert regime["did"] == pytest.approx(0.30)
    assert regime["verdict"] == VERDICT_MOVES
    assert regime["did_ci"][0] <= regime["did"] <= regime["did_ci"][1]


def test_regime_without_coverage_is_flagged() -> None:
    series = _series(_flat(10, 5, 1))
    regime = regime_comparison(
        series,
        series,
        ("2025-01-01", "2025-01-05"),
        ("2025-01-06", "2025-01-10"),
        min_seen=20,
    )

    assert regime["verdict"] == VERDICT_INSUFFICIENT
    assert regime["did"] is None


def test_monthly_coverage_counts_used_days_and_means() -> None:
    series = _series([(100, 10), (100, 20), (5, 2), (200, 40)])

    coverage = monthly_coverage(series, min_seen=20)

    assert coverage == [
        {
            "month": "2025-01",
            "days_present": 4,
            "days_used": 3,
            "mean_seen_per_day": pytest.approx(400 / 3),
            "mean_dark_per_day": pytest.approx(70 / 3),
            "dark_share": pytest.approx(0.175),
        }
    ]


def test_invalid_inputs_are_rejected() -> None:
    series = _series(_flat(10, 100, 10))
    with pytest.raises(ValueError):
        event_study(series, series, EVENTS, window_days=0)
    with pytest.raises(ValueError):
        event_study(series, series, EVENTS, min_seen=-1)
    with pytest.raises(ValueError):
        event_study([{"day": "2025-01-01", "seen": 1, "dark": 2}], series, EVENTS)
    with pytest.raises(ValueError):
        event_study(series, series, [{"date_start": "2025-02-02", "date_end": "2025-02-01"}])


@given(daily_series())
@settings(max_examples=40, deadline=None)
def test_property_did_is_zero_when_treated_equals_control(rows: list[dict]) -> None:
    result = event_study(rows, list(rows), EVENTS, n_boot=100, seed=0)[0]

    assert result["did"] == pytest.approx(0.0, abs=1e-12)
    assert result["did_ci"][0] <= result["did"] <= result["did_ci"][1]


@given(daily_series(), daily_series())
@settings(max_examples=40, deadline=None)
def test_property_ci_contains_the_point_estimate(treated: list[dict], control: list[dict]) -> None:
    result = event_study(treated, control, EVENTS, n_boot=100, seed=0)[0]

    assert result["verdict"] != VERDICT_INSUFFICIENT
    assert result["did"] is not None
    low, high = result["did_ci"]
    assert low <= result["did"] <= high


@given(daily_series())
@settings(max_examples=40, deadline=None)
def test_property_thin_series_are_flagged_as_insufficient(rows: list[dict]) -> None:
    thin = [{"day": row["day"], "seen": 10, "dark": row["dark"] % 11} for row in rows]

    result = event_study(thin, thin, EVENTS, min_seen=20, n_boot=50)[0]

    assert result["verdict"] == VERDICT_INSUFFICIENT
    assert result["did"] is None
    assert result["did_ci"] is None
