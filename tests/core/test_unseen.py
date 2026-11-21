"""Tests for the pure unseen-fleet aggregation (issue #88)."""

from __future__ import annotations

from datetime import date

import pytest

from darkfleet.core.unseen import daily_counts, density_grid, summary
from darkfleet.ingest.gfw import SarCellRow

D1 = date(2026, 10, 1)
D2 = date(2026, 10, 2)
D3 = date(2026, 10, 3)


def row(day: date, lat: float, lon: float, detections: int = 1) -> SarCellRow:
    """One radar cell; ``vessel_ids`` is irrelevant to the aggregation."""
    return SarCellRow(day=day, lat=lat, lon=lon, detections=detections, vessel_ids=1)


def test_daily_counts_sums_detections_and_splits_ais_match():
    matched = [row(D1, 25.0, 56.0, 2), row(D1, 25.1, 56.1, 3), row(D3, 25.2, 56.2, 2)]
    unmatched = [row(D1, 25.2, 56.2, 1), row(D2, 25.3, 56.3, 4)]

    daily = daily_counts(matched, unmatched)

    assert [item["day"] for item in daily] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    first, second, third = daily
    assert (first["broadcasting"], first["dark"], first["seen"]) == (5, 1, 6)
    assert first["dark_share"] == pytest.approx(1 / 6)
    assert (second["broadcasting"], second["dark"], second["seen"]) == (0, 4, 4)
    assert second["dark_share"] == pytest.approx(1.0)
    assert (third["broadcasting"], third["dark"], third["seen"]) == (2, 0, 2)
    assert third["dark_share"] == pytest.approx(0.0)


def test_daily_counts_always_balances():
    daily = daily_counts(
        [row(D1, 25.0, 56.0, 3)],
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 2)],
    )

    for item in daily:
        assert item["seen"] == item["broadcasting"] + item["dark"]
        assert 0.0 <= item["dark_share"] <= 1.0


def test_daily_counts_empty_window():
    assert daily_counts([], []) == []


def test_density_grid_uses_cell_centres_and_sums_detections():
    rows = [
        row(D1, 25.16, 56.58, 1),
        row(D1, 25.11, 56.51, 2),
        row(D1, 25.19, 56.59, 1),
        row(D1, 25.2, 56.6, 4),
    ]

    grid = density_grid(rows, cell_deg=0.1)

    assert grid == [
        {"lat": 25.15, "lon": 56.55, "n": 4},
        {"lat": 25.25, "lon": 56.65, "n": 4},
    ]


def test_density_grid_handles_negative_coordinates():
    grid = density_grid([row(D1, -0.05, -0.05, 2)], cell_deg=0.1)

    assert grid == [{"lat": -0.05, "lon": -0.05, "n": 2}]


def test_density_grid_rejects_non_positive_cell():
    with pytest.raises(ValueError):
        density_grid([], cell_deg=0)


def test_summary_totals_mean_and_busiest_day():
    daily = daily_counts(
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 1), row(D3, 25.0, 56.0, 8)],
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 3), row(D3, 25.0, 56.0, 2)],
    )

    result = summary(daily)

    assert result["days"] == 3
    assert result["seen_total"] == 16
    assert result["dark_total"] == 6
    assert result["dark_share_mean"] == pytest.approx((0.5 + 0.75 + 0.2) / 3)
    assert result["busiest_day"] == "2026-10-03"
    low, high = result["dark_share_ci"]
    assert low <= result["dark_share_mean"] <= high


def test_summary_bootstrap_is_seeded_and_deterministic():
    daily = daily_counts(
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 2), row(D3, 25.0, 56.0, 5)],
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 2), row(D3, 25.0, 56.0, 1)],
    )

    first = summary(daily)
    second = summary(daily)

    assert first == second
    assert first["dark_share_ci"] == summary(daily, seed=7)["dark_share_ci"]


def test_summary_ci_collapses_when_every_day_has_the_same_share():
    daily = daily_counts(
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 2)],
        [row(D1, 25.0, 56.0, 1), row(D2, 25.0, 56.0, 2)],
    )

    result = summary(daily)

    assert result["dark_share_mean"] == pytest.approx(0.5)
    assert result["dark_share_ci"] == pytest.approx([0.5, 0.5])


def test_summary_single_day_interval_is_the_share():
    daily = daily_counts([], [row(D1, 25.0, 56.0, 2)])

    result = summary(daily)

    assert result["dark_share_ci"] == pytest.approx([1.0, 1.0])


def test_summary_busiest_tie_goes_to_the_earliest_day():
    daily = daily_counts(
        [row(D2, 25.0, 56.0, 5), row(D1, 25.0, 56.0, 5)],
        [],
    )

    assert summary(daily)["busiest_day"] == "2026-10-01"


def test_summary_empty_window_is_zeroed_and_valid():
    result = summary([])

    assert result == {
        "days": 0,
        "seen_total": 0,
        "dark_total": 0,
        "dark_share_mean": 0.0,
        "dark_share_ci": [0.0, 0.0],
        "busiest_day": None,
    }
