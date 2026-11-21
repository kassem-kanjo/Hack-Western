"""Tests for the aisstream parser against the real Baltic recording fixture.

The fixture is an untrimmed 284-line slice of what the recorder wrote, so these tests pin the
behaviour the detectors rely on: message counts, nanosecond truncation, the AIS 511 heading
sentinel and the promise that junk lines are skipped instead of raising.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from darkfleet.core.tracks import Ping, VesselInfo
from darkfleet.ingest.ais_tracks import coverage, iter_files, parse_line

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ais" / "baltic_sample.jsonl"


def fixture_lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def line(number: int) -> str:
    """1-based line lookup so a test can name the message it pins."""
    return fixture_lines()[number - 1]


def position_report(**overrides: object) -> str:
    """A minimal class-B position line; overrides replace top-level message fields."""
    message = {
        "MessageID": 18,
        "RepeatIndicator": 0,
        "UserID": 211234567,
        "Latitude": 54.5,
        "Longitude": 10.25,
        "Sog": 4.2,
        "Cog": 91.0,
        "TrueHeading": 511,
        "Timestamp": 10,
    }
    message.update(overrides)
    record = {
        "MetaData": {
            "MMSI": 211234567,
            "ShipName": "TEST",
            "latitude": 54.5,
            "longitude": 10.25,
            "time_utc": "2026-11-21 06:00:00.123456789 +0000 UTC",
        },
        "MessageType": "StandardClassBPositionReport",
        "Message": {"StandardClassBPositionReport": message},
    }
    return json.dumps(record)


def test_fixture_parses_into_expected_counts() -> None:
    parsed = [obj for obj in (parse_line(raw) for raw in fixture_lines()) if obj is not None]

    assert len(parsed) == 284
    assert sum(isinstance(obj, Ping) for obj in parsed) == 145
    assert sum(isinstance(obj, VesselInfo) for obj in parsed) == 139
    assert len({obj.mmsi for obj in parsed}) == 12


def test_time_parsing_truncates_nanoseconds_to_microseconds() -> None:
    ping = parse_line(line(1))
    assert isinstance(ping, Ping)

    # "...04:59:57.096836168 +0000 UTC" keeps the first six fractional digits, unrounded.
    assert ping.t == datetime(2026, 11, 21, 4, 59, 57, 96836, tzinfo=UTC)
    assert ping.t.tzinfo is UTC


def test_position_report_maps_every_field() -> None:
    ping = parse_line(line(1))
    assert isinstance(ping, Ping)

    assert ping.mmsi == 276462000
    assert ping.lat == pytest.approx(55.53446666666667)
    assert ping.lon == pytest.approx(12.717166666666666)
    assert ping.sog == pytest.approx(7.7)
    assert ping.cog == pytest.approx(352.0)
    assert ping.heading == 358
    assert ping.nav_status == 0


def test_true_heading_511_becomes_none() -> None:
    # ARA is the first fixture vessel broadcasting the "not available" heading sentinel.
    ping = parse_line(line(11))
    assert isinstance(ping, Ping)
    assert ping.mmsi == 219005313
    assert ping.heading is None
    assert ping.nav_status == 0


def test_class_b_position_report_has_no_nav_status() -> None:
    parsed = parse_line(position_report())
    assert isinstance(parsed, Ping)

    assert parsed.mmsi == 211234567
    assert parsed.t == datetime(2026, 11, 21, 6, 0, 0, 123456, tzinfo=UTC)
    assert parsed.heading is None
    assert parsed.nav_status is None
    assert parsed.sog == pytest.approx(4.2)
    assert parsed.lon == pytest.approx(10.25)


def test_sog_102_3_means_not_available() -> None:
    # 102.3 kn is the AIS sentinel for "speed not available", not a real 102-knot ship.
    ping = parse_line(position_report(Sog=102.3))
    assert isinstance(ping, Ping)
    assert ping.sog is None


@pytest.mark.parametrize("sog", [102.2, 102.3, 150.0])
def test_sog_at_or_above_the_sentinel_becomes_none(sog: float) -> None:
    ping = parse_line(position_report(Sog=sog))
    assert isinstance(ping, Ping)
    assert ping.sog is None


@pytest.mark.parametrize("sog", [0.0, 38.0, 102.1])
def test_real_sog_below_the_sentinel_is_kept(sog: float) -> None:
    ping = parse_line(position_report(Sog=sog))
    assert isinstance(ping, Ping)
    assert ping.sog == pytest.approx(sog)


@pytest.mark.parametrize("cog", [0.0, 359.9])
def test_real_course_is_kept(cog: float) -> None:
    ping = parse_line(position_report(Cog=cog))
    assert isinstance(ping, Ping)
    assert ping.cog == pytest.approx(cog)


@pytest.mark.parametrize("cog", [360.0, 400.0])
def test_cog_at_or_above_360_means_not_available(cog: float) -> None:
    ping = parse_line(position_report(Cog=cog))
    assert isinstance(ping, Ping)
    assert ping.cog is None


def test_ship_static_data_maps_static_fields() -> None:
    info = parse_line(line(3))
    assert isinstance(info, VesselInfo)

    assert info.mmsi == 266128000
    assert info.imo == 9096806
    assert info.name == "VALKYRIA"
    assert info.callsign == "SMPK"
    assert info.ship_type == 30
    assert info.length_m == pytest.approx(18.0)  # Dimension A=15 + B=3
    assert info.destination == "-"
    assert info.last_seen == datetime(2026, 11, 21, 4, 59, 57, 214439, tzinfo=UTC)


def test_imo_zero_and_padded_strings_are_normalised() -> None:
    info = parse_line(line(6))
    assert isinstance(info, VesselInfo)

    assert info.imo is None  # 0 is AIS "not available"
    assert info.name == "BUNTHAUS"  # trailing padding stripped
    assert info.callsign == "DA6185"
    assert info.destination == "HAMBURG"
    assert info.length_m == pytest.approx(15.0)


def test_static_data_report_part_b() -> None:
    record = {
        "MetaData": {
            "MMSI": 265500000,
            "time_utc": "2026-11-21 06:01:02.500000000 +0000 UTC",
        },
        "MessageType": "StaticDataReport",
        "Message": {
            "StaticDataReport": {
                "MessageID": 24,
                "PartNumber": 1,
                "ShipType": 60,
                "CallSign": "SB1234 ",
                "Dimension": {"A": 8, "B": 4, "C": 2, "D": 2},
            }
        },
    }

    info = parse_line(json.dumps(record))
    assert isinstance(info, VesselInfo)
    assert info.mmsi == 265500000
    assert info.name is None  # part B carries no name
    assert info.callsign == "SB1234"
    assert info.ship_type == 60
    assert info.length_m == pytest.approx(12.0)
    assert info.imo is None


def test_junk_and_unused_messages_return_none() -> None:
    unknown = {
        "MetaData": {
            "MMSI": 123456789,
            "time_utc": "2026-11-21 06:00:00.000000000 +0000 UTC",
        },
        "MessageType": "AidToNavigationReport",
        "Message": {"AidToNavigationReport": {"Name": "BUOY"}},
    }

    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("not json {") is None
    assert parse_line("[]") is None
    assert parse_line('{"MessageType": "PositionReport"}') is None
    assert parse_line(json.dumps(unknown)) is None
    # A position message missing its coordinates is unusable, not an error.
    broken = json.loads(position_report())
    del broken["Message"]["StandardClassBPositionReport"]["Latitude"]
    assert parse_line(json.dumps(broken)) is None


def _write_recording(directory: Path, name: str, lines: list[str]) -> Path:
    path = directory / name
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
    return path


def test_iter_files_reads_gzip_and_yields_time_order(tmp_path: Path) -> None:
    recording = tmp_path / "BALTIC_WATCH"
    recording.mkdir()
    _write_recording(recording, "20261121T05.jsonl.gz", fixture_lines())

    parsed = list(iter_files([recording]))

    assert len(parsed) == 284
    assert sum(isinstance(obj, Ping) for obj in parsed) == 145
    times = [obj.t if isinstance(obj, Ping) else obj.last_seen for obj in parsed]
    assert all(isinstance(t, datetime) for t in times)
    assert times == sorted(times)
    assert times[0] == datetime(2026, 11, 21, 4, 59, 57, 96836, tzinfo=UTC)


def test_iter_files_merges_hour_files_by_timestamp(tmp_path: Path) -> None:
    lines = fixture_lines()
    # The later half is written to the alphabetically first file: concatenating filenames
    # would be out of order, so only a timestamp merge passes.
    _write_recording(tmp_path, "a_late.jsonl.gz", lines[142:])
    _write_recording(tmp_path, "b_early.jsonl.gz", lines[:142])

    parsed = list(iter_files([tmp_path]))
    times = [obj.t if isinstance(obj, Ping) else obj.last_seen for obj in parsed]

    assert len(parsed) == 284
    assert times == sorted(times)


def test_iter_files_ignores_other_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "partial.jsonl").write_text(line(1), encoding="utf-8")
    _write_recording(tmp_path, "20261121T05.jsonl.gz", fixture_lines()[:3])

    parsed = list(iter_files([tmp_path, tmp_path / "notes.txt"]))
    assert len(parsed) == 3


def test_coverage_counts_and_zero_fills_minutes() -> None:
    base = datetime(2026, 11, 21, 5, 0, tzinfo=UTC)
    times = [base + timedelta(seconds=10), base + timedelta(seconds=50), base + timedelta(minutes=2)]

    minutes = coverage(times)

    assert [m.minute for m in minutes] == [
        base,
        base + timedelta(minutes=1),
        base + timedelta(minutes=2),
    ]
    assert [m.n_msgs for m in minutes] == [2, 0, 1]


def test_coverage_normalises_offsets_to_utc_minutes() -> None:
    plus_two = timezone(timedelta(hours=2))
    minutes = coverage([datetime(2026, 11, 21, 7, 0, 30, tzinfo=plus_two)])
    assert [m.minute for m in minutes] == [datetime(2026, 11, 21, 5, 0, tzinfo=UTC)]


def test_coverage_of_nothing_is_empty() -> None:
    assert coverage([]) == []
