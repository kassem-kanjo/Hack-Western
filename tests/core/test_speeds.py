"""Tests for per-class maximum vessel speeds."""

import pytest

from darkfleet.core.speeds import MARGIN, max_speed_knots
from darkfleet.core.tracks import ship_class


@pytest.mark.parametrize(
    ("vessel_type", "base"),
    [
        ("cargo", 14.0),
        ("bulk", 14.0),
        ("tanker", 15.0),
        ("fishing", 12.0),
        ("carrier", 15.0),
        ("passenger", 20.0),
        ("hsc", 42.0),
        ("service", 30.0),
        ("other_non_fishing", 14.0),
    ],
)
def test_listed_class_uses_its_base_speed(vessel_type: str, base: float):
    assert max_speed_knots(vessel_type) == pytest.approx(base * MARGIN)


@pytest.mark.parametrize(
    "vessel_type",
    [None, "", "inactive", "insufficient_data", "unknown", "INACTIVE"],
)
def test_unknown_class_uses_default(vessel_type: str | None):
    assert max_speed_knots(vessel_type) == pytest.approx(14.0 * MARGIN)


@pytest.mark.parametrize(
    ("vessel_type", "expected"),
    [
        ("tanker", 17.25),
        ("cargo", 16.1),
        ("fishing", 13.8),
        ("passenger", 23.0),
        ("hsc", 48.3),
        ("service", 34.5),
        (" Cargo ", 16.1),
    ],
)
def test_known_expectations(vessel_type: str, expected: float):
    assert max_speed_knots(vessel_type) == pytest.approx(expected)


def test_every_return_is_positive():
    for vessel_type in (
        "cargo",
        "bulk",
        "tanker",
        "fishing",
        "carrier",
        "passenger",
        "hsc",
        "service",
        "other_non_fishing",
        None,
        "",
        "inactive",
        "insufficient_data",
        "nonsense",
    ):
        assert max_speed_knots(vessel_type) > 0


@pytest.mark.parametrize(
    ("ship_type", "expected"),
    [
        (40, "hsc"),
        (42, "hsc"),
        (49, "hsc"),
        (50, "service"),
        (52, "service"),
        (59, "service"),
        (60, "passenger"),
        (70, "cargo"),
        (80, "tanker"),
        (30, "fishing"),
        (31, "default"),
        (None, "default"),
    ],
)
def test_ship_type_maps_to_the_speed_class(ship_type: int | None, expected: str):
    assert ship_class(ship_type) == expected
