"""Tests for issue #86: listening and watched areas on the watch screen.

The boxes are hand-drawn, so the tests do not check legal boundaries. They check the three
things the UI depends on: a point in a known strait or port approach is attributed to the
narrow watched area rather than the wide listening box around it, open ocean is attributed to
nothing, and the listening table cannot silently drift from the recorder's own region list.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from darkfleet.core.anchorages import STS_HOTSPOTS
from darkfleet.core.areas import LISTENING, WATCHED, area_of
from darkfleet.ingest.regions import REGIONS as INGEST_REGIONS


def _area(area_id: str):
    for area in [*WATCHED, *LISTENING]:
        if area["id"] == area_id:
            return area
    raise AssertionError(f"area {area_id} missing")


def _bbox_of(region: dict) -> list[float]:
    """Bounding box of a GeoJSON polygon in [min_lon, min_lat, max_lon, max_lat] order."""
    ring = region["coordinates"][0]
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]
    return [min(lons), min(lats), max(lons), max(lats)]


def test_strait_of_hormuz_is_the_watched_area_not_the_listening_box() -> None:
    # 26.5 N 56.3 E is inside both HORMUZ and HORMUZ_STRAIT; watched must win.
    assert area_of(26.5, 56.3) == "HORMUZ_STRAIT"


def test_primorsk_is_a_gulf_of_finland_port_approach() -> None:
    assert area_of(60.35, 28.6) == "GULF_OF_FINLAND_PORTS"


def test_eopl_kerch_and_laconia_are_watched() -> None:
    assert area_of(1.35, 104.5) == "EOPL_STS"
    assert area_of(45.2, 36.5) == "KERCH"
    assert area_of(36.5, 22.8) == "LACONIA"


def test_mid_atlantic_is_no_area_at_all() -> None:
    assert area_of(30.0, -40.0) is None


def test_a_listening_area_still_answers_outside_any_watched_box() -> None:
    # Central Baltic, far from every watched box but inside the recorder's subscription.
    assert area_of(58.0, 19.0) == "BALTIC_WATCH"


def test_eopl_watched_box_reuses_the_anchorage_hot_spot() -> None:
    hotspot = next(item for item in STS_HOTSPOTS if item["name"] == "EOPL")

    assert _area("EOPL_STS")["bbox"] == hotspot["bbox"]


def test_listening_boxes_match_the_in_package_region_registry() -> None:
    # scripts/record_ais.py restates these boxes; darkfleet.ingest.regions is the copy the
    # package itself uses, so a drift there means the recorder list changed without us.
    assert _area("BALTIC_WATCH")["bbox"] == _bbox_of(INGEST_REGIONS["BALTIC_WATCH"])
    # The measured Hormuz area (#266) is narrower than the listening box: it stops west of the
    # Gulf of Oman comparison area, which the recorder must still hear.
    listen_w, listen_s, listen_e, listen_n = _area("HORMUZ")["bbox"]
    measured_w, measured_s, measured_e, measured_n = _bbox_of(INGEST_REGIONS["HORMUZ"])
    assert listen_w <= measured_w and measured_e <= listen_e
    assert listen_s <= measured_s and measured_n <= listen_n


def test_ids_are_unique_across_both_tables() -> None:
    ids = [area["id"] for area in [*LISTENING, *WATCHED]]

    assert len(ids) == len(set(ids))


def test_every_box_is_well_formed() -> None:
    for area in [*LISTENING, *WATCHED]:
        assert area["id"] and area["name"]
        assert area["kind"] in {"listening", "watched"}
        min_lon, min_lat, max_lon, max_lat = area["bbox"]
        assert min_lon < max_lon
        assert min_lat < max_lat
        assert -180.0 <= min_lon <= 180.0 and -180.0 <= max_lon <= 180.0
        assert -90.0 <= min_lat <= 90.0 and -90.0 <= max_lat <= 90.0


@given(
    lat=st.floats(24.0, 27.2, allow_nan=False, allow_infinity=False),
    lon=st.floats(55.0, 57.6, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, deadline=None)
def test_any_point_inside_a_watched_box_has_an_area(lat: float, lon: float) -> None:
    assert area_of(lat, lon) is not None
