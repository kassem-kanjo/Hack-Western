"""Offline tests for the OpenSanctions maritime loader and matcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from darkfleet.ingest.sanctions import (
    NAME_MATCH_NOTE,
    SANCTIONS_SOURCE,
    build_index,
    load_vessels,
    match_vessel,
    normalise_name,
)

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sanctions" / "maritime_sample.csv"


def _vessel(
    caption: str,
    risk: list[str],
    mmsi: str = "",
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "caption": caption,
        "imo": "",
        "mmsi": mmsi,
        "risk": risk,
        "aliases": aliases or [],
        "url": f"https://www.opensanctions.org/entities/{caption}",
        "datasets": ["test_dataset"],
    }


def test_load_vessels_normalises_every_field() -> None:
    vessels = load_vessels(SAMPLE)

    assert len(vessels) == 59
    first = vessels[0]
    assert set(first) == {"caption", "imo", "mmsi", "risk", "aliases", "url", "datasets"}
    assert first["caption"] == "BOUDREAUX TIDE"
    assert first["imo"] == "9427366"  # "IMO9427366" -> digits
    assert first["mmsi"] == ""  # the sample rows carry no MMSI
    assert first["risk"] == ["mare.detained", "reg.warn"]
    assert first["aliases"] == []
    assert first["datasets"] == ["abuja_mou_detention"]
    assert first["url"].startswith("https://www.opensanctions.org/entities/")
    assert all(vessel["caption"] for vessel in vessels)


def test_load_vessels_keeps_vessel_rows_only(tmp_path: Path) -> None:
    source = tmp_path / "maritime.csv"
    source.write_text(
        "type,caption,imo,risk,countries,flag,mmsi,id,url,datasets,aliases\n"
        'VESSEL,"KEPT",IMO1234567,sanction,,pa,123456789,id-1,url-1,ds-1,"ALIAS ONE;ALIAS TWO"\n'
        'COMPANY,"NOT A SHIP",,,,,,,,,,\n'
        ',"NO TYPE",,,,,,,,,,\n',
        encoding="utf-8",
    )

    vessels = load_vessels(source)

    assert [vessel["caption"] for vessel in vessels] == ["KEPT"]
    assert vessels[0]["aliases"] == ["ALIAS ONE", "ALIAS TWO"]


def test_mmsi_match_wins() -> None:
    vessels = load_vessels(SAMPLE)
    vessels[0]["mmsi"] = "538001234"
    index = build_index(vessels)

    hit = match_vessel(index, "538-001-234", "MT UNGIESHI")

    assert hit is not None
    assert hit["method"] == "mmsi"
    assert hit["entry"]["caption"] == "BOUDREAUX TIDE"
    assert hit["entry"]["mmsi"] == "538001234"
    assert hit["source"] == SANCTIONS_SOURCE
    assert "note" not in hit


def test_name_match_is_normalised_and_flagged_as_weak() -> None:
    index = build_index(load_vessels(SAMPLE))

    hit = match_vessel(index, "", "  mt-ungieshi ")

    assert hit is not None
    assert hit["method"] == "name"
    assert hit["entry"]["caption"] == "MT UNGIESHI"
    assert hit["note"] == NAME_MATCH_NOTE
    assert hit["category"] == "detained"


def test_alias_matches_like_a_caption() -> None:
    alias_vessel = _vessel("PRIMARY NAME", ["sanction"], aliases=["SECOND NAME"])
    index = build_index([alias_vessel])

    assert match_vessel(index, None, normalise_name("second name"))["entry"]["caption"] == "PRIMARY NAME"


def test_no_match_returns_none() -> None:
    index = build_index(load_vessels(SAMPLE))

    assert match_vessel(index, "000000000", "NOT A LISTED SHIP") is None


def test_category_follows_the_strongest_risk_tag() -> None:
    cases = [
        (["sanction", "mare.detained"], "sanctioned"),
        (["mare.shadow", "reg.warn"], "shadow_fleet"),
        (["mare.detained", "reg.warn"], "detained"),
        (["reg.warn"], "other"),
        ([], "other"),
    ]
    for risk, expected in cases:
        index = build_index([_vessel("SHIP", risk)])
        hit = match_vessel(index, "", "SHIP")
        assert hit is not None
        assert hit["category"] == expected


def test_entry_exposes_the_documented_fields() -> None:
    index = build_index(load_vessels(SAMPLE))

    entry = match_vessel(index, "", "BOUDREAUX TIDE")["entry"]

    assert set(entry) == {"caption", "imo", "mmsi", "risk", "url", "datasets"}
    assert entry["risk"] == ["mare.detained", "reg.warn"]
