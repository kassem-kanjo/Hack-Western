"""Offline tests for the Baltic IMO watchlist join (issue #76)."""

from __future__ import annotations

from pathlib import Path

from darkfleet.core.tracks import VesselInfo
from darkfleet.ingest.watchlist import imo_watchlist

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sanctions" / "maritime_sample.csv"


def test_imo_hit_is_strength_imo_and_reports_the_entry() -> None:
    # The name is deliberately wrong: the hull number is what identifies the ship.
    vessels = [VesselInfo(mmsi=211000001, imo=9427366, name="RENAMED UNDER A NEW FLAG")]

    watchlist = imo_watchlist(vessels, SAMPLE)

    assert set(watchlist) == {211000001}
    case = watchlist[211000001]
    assert case["mmsi"] == 211000001
    assert case["imo"] == 9427366
    assert case["name"] == "RENAMED UNDER A NEW FLAG"
    assert case["strength"] == "imo"
    assert case["category"] == "detained"
    entry = case["entry"]
    assert entry["caption"] == "BOUDREAUX TIDE"
    assert entry["imo"] == "9427366"  # the list stores digits only ("IMO9427366" -> 9427366)
    assert entry["risk"] == ["mare.detained", "reg.warn"]
    assert entry["url"].startswith("https://www.opensanctions.org/entities/")
    assert entry["datasets"] == ["abuja_mou_detention"]


def test_unknown_imo_and_generic_name_is_not_listed() -> None:
    vessels = [VesselInfo(mmsi=211000002, imo=9999999, name="NORDIC STAR")]

    assert imo_watchlist(vessels, SAMPLE) == {}


def test_missing_imo_falls_back_to_the_name_path() -> None:
    vessels = [VesselInfo(mmsi=211000003, imo=None, name="  mt-ungieshi ")]

    case = imo_watchlist(vessels, SAMPLE)[211000003]

    assert case["strength"] == "weak_name"  # normalised name hit, no flag evidence
    assert case["category"] == "detained"
    assert case["entry"]["caption"] == "MT UNGIESHI"
    assert case["imo"] is None


def test_name_with_a_hull_number_is_strong_even_without_an_imo() -> None:
    vessels = [VesselInfo(mmsi=211000004, imo=None, name="GMTS TRACKER 01")]

    assert imo_watchlist(vessels, SAMPLE)[211000004]["strength"] == "strong_name"


def test_imo_miss_still_retries_on_the_name() -> None:
    # IMO 1234567 is not in the sample, but the name is, so the fallback should still find it.
    vessels = [VesselInfo(mmsi=211000005, imo=1234567, name="BBC OLYMPUS")]

    case = imo_watchlist(vessels, SAMPLE)[211000005]

    assert case["strength"] == "weak_name"
    assert case["entry"]["caption"] == "BBC OLYMPUS"
    assert case["imo"] == 1234567


def test_only_sanctioned_shadow_fleet_and_detained_are_kept(tmp_path: Path) -> None:
    source = tmp_path / "maritime.csv"
    source.write_text(
        "type,caption,imo,risk,countries,flag,mmsi,id,url,datasets,aliases\n"
        'VESSEL,"PAPERWORK ONLY",IMO1111111,reg.warn,,pa,,,,url-1,ds-1,\n'
        'VESSEL,"SANCTIONED SHIP",IMO2222222,sanction,,pa,,,,url-2,ds-2,\n'
        'VESSEL,"SHADOW SHIP",IMO3333333,mare.shadow.flag,,pa,,,,url-3,ds-3,\n',
        encoding="utf-8",
    )
    vessels = [
        VesselInfo(mmsi=211000006, imo=1111111, name="PAPERWORK ONLY"),
        VesselInfo(mmsi=211000007, imo=2222222, name="SANCTIONED SHIP"),
        VesselInfo(mmsi=211000008, imo=3333333, name="SHADOW SHIP"),
    ]

    watchlist = imo_watchlist(vessels, source)

    assert set(watchlist) == {211000007, 211000008}
    assert watchlist[211000007]["category"] == "sanctioned"
    assert watchlist[211000008]["category"] == "shadow_fleet"


def test_one_dict_entry_per_mmsi() -> None:
    vessels = [
        VesselInfo(mmsi=211000009, imo=9427366, name="BOUDREAUX TIDE"),
        VesselInfo(mmsi=211000010, imo=None, name="NOT ON THE LIST"),
    ]

    watchlist = imo_watchlist(vessels, SAMPLE)

    assert list(watchlist) == [211000009]
