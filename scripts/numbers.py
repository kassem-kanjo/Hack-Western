"""Collect every number the pitch, README and Devpost quote into one sheet.

    uv run python scripts/numbers.py [--snapshot-dir data/snapshot]
        [--validation data/validation/tracks_results.json]
        [--out-md out/numbers.md] [--out-json out/numbers.json]

The demo is only honest if the words in ``docs/demo-script.md`` and the figures in
``README.md`` come from the same snapshot the console serves. This script reads that
snapshot and writes ``out/numbers.md`` (one line per number, with the file and JSON path
it came from) plus ``out/numbers.json`` (the same values keyed for scripts).

Each value is recomputed the way the UI reads it, so the sheet and the screen can never
disagree: the Hormuz share is the median headline share rounded like ``Math.round``, a
live region's window is the same one-decimal hour span presentation mode prints, and the
watchlist count uses the same alert categories. Tiger-only facts (the positions count,
for example) are deliberately absent: they need the database, not the snapshot.

``--merge-stats PATH`` is the small helper ``scripts/sunday.sh`` uses after exporting
fresh aggregates from Tiger: it copies only ``ais_hourly``, ``sar_weekly`` and ``source``
from PATH into ``<snapshot-dir>/stats.json`` and leaves every other key untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "data" / "snapshot"
DEFAULT_VALIDATION = ROOT / "data" / "validation" / "tracks_results.json"
DEFAULT_OUT_MD = ROOT / "out" / "numbers.md"
DEFAULT_OUT_JSON = ROOT / "out" / "numbers.json"

LIVE_REGIONS = ("BALTIC_WATCH", "MALAYSIA_EOPL", "BLACK_SEA")
EVENT_KINDS = (
    ("silence", "silences"),
    ("jump", "jumps"),
    ("clone", "clones"),
    ("meeting", "meetings"),
    ("spoof", "spoofs"),
)
ALERT_CATEGORIES = frozenset({"sanctioned", "shadow_fleet"})
"""The same two categories ``watchlistAlerts`` paints red in the web client."""

STATS_KEYS = ("ais_hourly", "sar_weekly", "source")
"""The aggregate keys a Tiger export replaces; ``validation`` and dark hours stay put."""

MISSING = "missing"


# --------------------------------------------------------------------------- reading


def _read_json(path: Path) -> Any:
    """A parsed JSON value, or None when the file is absent or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    """A finite JSON number (bools are not numbers here), or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(round(number))


def _js_round(value: float) -> int:
    """``Math.round``: halves go up, so the sheet matches what the browser prints."""
    return int(math.floor(value + 0.5))


def _pc(value: Any, digits: int = 1) -> float | None:
    number = _number(value)
    return None if number is None else round(number * 100.0, digits)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _window_hours(window: dict[str, Any]) -> tuple[float, int] | None:
    """The span the UI calls ``window_h``: one decimal, then rounded to whole hours."""
    start = _parse_time(window.get("start"))
    end = _parse_time(window.get("end"))
    if start is None or end is None:
        return None
    hours = (end - start).total_seconds() / 3600.0
    one_decimal = _js_round(hours * 10) / 10.0
    return one_decimal, _js_round(one_decimal)


def _display(path: Path) -> str:
    """Repo-relative where possible, else the path as given; no local home directory."""
    # absolute(), not resolve(): a symlinked data/ folder must still print as data/...
    try:
        return path.absolute().relative_to(ROOT.absolute()).as_posix()
    except ValueError:
        return "/".join(path.parts[-2:])


# --------------------------------------------------------------------------- values


def _watchlist_alerts(watchlist: Any) -> int:
    """How many red tags the live screen would show, whatever shape the snapshot used."""
    if isinstance(watchlist, dict):
        alerts = watchlist.get("alerts")
        return len(alerts) if isinstance(alerts, list) else 0
    if isinstance(watchlist, list):
        return sum(1 for row in watchlist if _object(row).get("category") in ALERT_CATEGORIES)
    return 0


def _event_counts(payload: dict[str, Any]) -> dict[str, tuple[int, str]]:
    """Counts per kind and the path each came from: counters first, else the event list."""
    counts = _object(payload.get("counts"))
    events = payload.get("events")
    events = events if isinstance(events, list) else []
    out: dict[str, tuple[int, str]] = {}
    for kind, plural in EVENT_KINDS:
        value = _int(counts.get(plural))
        if value is not None:
            out[kind] = (value, f"counts.{plural}")
        else:
            out[kind] = (
                sum(1 for event in events if _object(event).get("kind") == kind),
                "events[].kind",
            )
    return out


def _top_spoof(events: Any) -> dict[str, Any] | None:
    """The spoof event presentation mode talks about: the highest severity."""
    if not isinstance(events, list):
        return None
    best: dict[str, Any] | None = None
    best_severity = -math.inf
    for event in events:
        row = _object(event)
        if row.get("kind") != "spoof":
            continue
        severity = _number(row.get("severity"))
        if severity is None:
            severity = 0.0
        if best is None or severity > best_severity:
            best = row
            best_severity = severity
    return best


def _offset_range(evidence: dict[str, Any]) -> tuple[float | None, float | None]:
    """The smallest and largest jump the spoof event quotes, in km."""
    rows = evidence.get("vessels")
    offsets: list[float] = []
    if isinstance(rows, list):
        for row in rows:
            value = _number(_object(row).get("offset_km"))
            if value is not None:
                offsets.append(value)
    if not offsets:
        single = _number(evidence.get("offset_km"))
        if single is not None:
            offsets.append(single)
    if not offsets:
        return None, None
    return round(min(offsets), 1), round(max(offsets), 1)


def _spoof_ships(evidence: dict[str, Any]) -> int | None:
    """``n_vessels`` when authoritative, the listed MMSIs otherwise, as the UI does."""
    count = _int(evidence.get("n_vessels"))
    if count is not None:
        return count
    rows = evidence.get("vessels")
    if isinstance(rows, list):
        return len(rows)
    return _int(evidence.get("count"))


def _unexplained(totals: dict[str, Any], cases: Any) -> int | None:
    count = _int(totals.get("candidates_final"))
    if count is not None:
        return count
    if not isinstance(cases, list):
        return None
    return sum(1 for case in cases if _object(case).get("status") == "candidate")


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


# --------------------------------------------------------------------------- collection


class Sheet:
    """Ordered number lines plus a flat ``values`` map for other scripts."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []
        self.values: dict[str, Any] = {}

    def add(self, key: str, name: str, value: Any, source: Path, path: str) -> None:
        if value is None:
            return
        self.values[key] = value
        self.lines.append(
            {
                "key": key,
                "name": name,
                "value": value,
                "source": _display(source),
                "path": path,
            }
        )


def _add_hormuz(sheet: Sheet, path: Path, payload: dict[str, Any]) -> None:
    summary = _object(payload.get("summary"))
    period = _object(payload.get("period"))
    share = _first_number(summary.get("headline_share"), summary.get("dark_share_mean"))
    days_used = _first_number(summary.get("n_days_used"), summary.get("days"))
    days_path = "summary.n_days_used" if _number(summary.get("n_days_used")) is not None else "summary.days"
    share_path = (
        "summary.headline_share"
        if _number(summary.get("headline_share")) is not None
        else "summary.dark_share_mean"
    )
    if share is not None:
        sheet.add(
            "hormuz.share_pct",
            "Hormuz share (%), median well-covered day",
            _js_round(share * 100),
            path,
            share_path,
        )
        sheet.add(
            "hormuz.share_median",
            "Hormuz median dark share (fraction)",
            round(share, 4),
            path,
            share_path,
        )
    sheet.add(
        "hormuz.days_used",
        "Hormuz well-covered days used",
        None if days_used is None else int(days_used),
        path,
        days_path,
    )
    sheet.add(
        "hormuz.days_total",
        "Hormuz days in window",
        _int(summary.get("days")),
        path,
        "summary.days",
    )
    start = period.get("start")
    end = period.get("end")
    if isinstance(start, str) and isinstance(end, str):
        sheet.add("hormuz.date_start", "Hormuz window start", start, path, "period.start")
        sheet.add("hormuz.date_end", "Hormuz window end", end, path, "period.end")
        sheet.add(
            "hormuz.date_range",
            "Hormuz date range",
            f"{start} to {end}",
            path,
            "period.start, period.end",
        )


def _add_live(sheet: Sheet, path: Path, region: str, payload: dict[str, Any]) -> None:
    counts = _object(payload.get("counts"))
    sheet.add(
        f"live.{region}.vessels",
        f"{region} vessels heard",
        _int(counts.get("vessels")),
        path,
        "counts.vessels",
    )
    hours = _window_hours(_object(payload.get("window")))
    if hours is not None:
        one_decimal, whole = hours
        sheet.add(
            f"live.{region}.window_hours",
            f"{region} window hours",
            one_decimal,
            path,
            "window.start, window.end",
        )
        sheet.add(
            f"live.{region}.window_h",
            f"{region} window, rounded hours",
            whole,
            path,
            "window.start, window.end",
        )
    for kind, (count, count_path) in _event_counts(payload).items():
        sheet.add(
            f"live.{region}.events.{kind}",
            f"{region} {kind} events",
            count,
            path,
            count_path,
        )
    sheet.add(
        f"live.{region}.watchlist_alerts",
        f"{region} watchlist alerts",
        _watchlist_alerts(payload.get("watchlist")),
        path,
        "watchlist.alerts",
    )


def _add_spoof(sheet: Sheet, path: Path, payload: dict[str, Any]) -> None:
    event = _top_spoof(payload.get("events"))
    if event is None:
        return
    evidence = _object(event.get("evidence"))
    sheet.add(
        "spoof.ships",
        "Spoof event ships",
        _spoof_ships(evidence),
        path,
        "events[].evidence.n_vessels",
    )
    low, high = _offset_range(evidence)
    sheet.add(
        "spoof.offset_km_min",
        "Spoof jump offset minimum (km)",
        low,
        path,
        "events[].evidence.vessels[].offset_km",
    )
    sheet.add(
        "spoof.offset_km_max",
        "Spoof jump offset maximum (km)",
        high,
        path,
        "events[].evidence.vessels[].offset_km",
    )
    sheet.add(
        "spoof.event_start",
        "Spoof event first report",
        event.get("start"),
        path,
        "events[].start",
    )


def _add_funnel(sheet: Sheet, path: Path, payload: dict[str, Any]) -> None:
    totals = _object(payload.get("totals"))
    sheet.add(
        "funnel.gaps_scanned",
        "Funnel: gaps scanned",
        _int(totals.get("gaps_scanned")),
        path,
        "totals.gaps_scanned",
    )
    sheet.add(
        "funnel.with_candidates",
        "Funnel: radar inside prism",
        _int(totals.get("with_candidates")),
        path,
        "totals.with_candidates",
    )
    sheet.add(
        "funnel.unexplained",
        "Funnel: unexplained after physics",
        _unexplained(totals, payload.get("cases")),
        path,
        "totals.candidates_final",
    )
    sheet.add(
        "funnel.ruled_out",
        "Funnel: cleared with a reason",
        _int(totals.get("ruled_out")),
        path,
        "totals.ruled_out",
    )


def _add_validation(sheet: Sheet, path: Path, payload: dict[str, Any]) -> None:
    containment = _object(payload.get("containment"))
    total = _int(containment.get("n"))
    inside = _int(containment.get("inside_lens_at_truth_time"))
    sheet.add(
        "validation.containment_n",
        "Validation: hidden windows inside the lens",
        inside,
        path,
        "containment.inside_lens_at_truth_time",
    )
    sheet.add(
        "validation.containment_total",
        "Validation: hidden windows scored",
        total,
        path,
        "containment.n",
    )
    sheet.add(
        "validation.containment_pct",
        "Validation: containment (%)",
        _pc(containment.get("rate")),
        path,
        "containment.rate",
    )

    false_positive = _object(payload.get("false_positive"))
    sheet.add(
        "validation.false_positive_events",
        "Validation: jump/clone false positives",
        _int(false_positive.get("events")),
        path,
        "false_positive.events",
    )
    vessel_days = _number(false_positive.get("vessel_days"))
    sheet.add(
        "validation.false_positive_vessel_days",
        "Validation: clean vessel-days observed",
        None if vessel_days is None else round(vessel_days, 1),
        path,
        "false_positive.vessel_days",
    )
    sheet.add(
        "validation.false_positive_per_vessel_day",
        "Validation: false positives per vessel-day",
        _number(false_positive.get("per_vessel_day")),
        path,
        "false_positive.per_vessel_day",
    )

    detection = _object(payload.get("detection"))
    found = _int(detection.get("found_underway"))
    injected = _int(detection.get("injected_underway"))
    sheet.add(
        "validation.recall_found",
        "Validation: in-scope gaps found",
        found,
        path,
        "detection.found_underway",
    )
    sheet.add(
        "validation.recall_injected",
        "Validation: in-scope gaps injected",
        injected,
        path,
        "detection.injected_underway",
    )
    sheet.add(
        "validation.recall_pct",
        "Validation: recall within scope (%)",
        _pc(detection.get("recall_underway")),
        path,
        "detection.recall_underway",
    )

    bridge = _object(payload.get("bridge"))
    sheet.add(
        "validation.bridge_auc",
        "Validation: Brownian bridge AUC",
        _number(bridge.get("auc")),
        path,
        "bridge.auc",
    )


def _add_stats(sheet: Sheet, path: Path, payload: dict[str, Any]) -> None:
    sheet.add("stats.source", "Stats backend", payload.get("source"), path, "source")
    validation = _object(payload.get("validation"))
    sheet.add(
        "stats.null_ratio",
        "Radar vs chance ratio",
        _number(validation.get("null_ratio")),
        path,
        "validation.null_ratio",
    )
    sheet.add(
        "stats.bridge_auc",
        "Snapshot bridge AUC",
        _number(validation.get("bridge_auc")),
        path,
        "validation.bridge_auc",
    )
    for key, name in (
        ("dark_hours_daily", "Stats dark-hour days"),
        ("sar_weekly", "Stats SAR weeks"),
        ("ais_hourly", "Stats hourly buckets"),
    ):
        rows = payload.get(key)
        if isinstance(rows, list):
            sheet.add(f"stats.{key}_count", name, len(rows), path, key)


def collect(snapshot_dir: Path, validation: Path) -> tuple[Sheet, list[str]]:
    """Read every documented source and return the sheet plus any missing file names."""
    sheet = Sheet()
    missing: list[str] = []
    sources: list[tuple[Path, Any]] = [
        (snapshot_dir / "hormuz_unseen.json", _add_hormuz),
        (snapshot_dir / "cases.json", _add_funnel),
        (snapshot_dir / "stats.json", _add_stats),
    ]
    for path, builder in sources:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            missing.append(_display(path))
            continue
        builder(sheet, path, payload)

    for region in LIVE_REGIONS:
        path = snapshot_dir / f"live_{region}.json"
        payload = _read_json(path)
        if not isinstance(payload, dict):
            missing.append(_display(path))
            continue
        _add_live(sheet, path, region, payload)
        if region == "BALTIC_WATCH":
            _add_spoof(sheet, path, payload)

    payload = _read_json(validation)
    if isinstance(payload, dict):
        _add_validation(sheet, validation, payload)
    else:
        missing.append(_display(validation))
    return sheet, missing


# --------------------------------------------------------------------------- output


def _format(value: Any) -> str:
    """Human spelling for the markdown table; the JSON keeps the raw value."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text if text else "0"
    return str(value).replace("|", "\\|")


def _render_markdown(sheet: Sheet, missing: Sequence[str], generated_at: str) -> str:
    lines = [
        "# Numbers",
        "",
        f"Generated: {generated_at}",
        "",
        "Every figure below is read from the Sunday snapshot, with the file and JSON path it",
        "came from. The pitch and the console use the same fields, so a number here is never",
        "a number the room cannot see on screen.",
        "",
        "| Number | Value | Source | JSON path |",
        "| --- | --- | --- | --- |",
    ]
    for line in sheet.lines:
        lines.append(f"| {line['name']} | {_format(line['value'])} | `{line['source']}` | `{line['path']}` |")
    if not sheet.lines:
        lines.append("| No data | - | - | - |")
    if missing:
        lines.extend(["", "## Missing sources", ""])
        lines.extend(f"- `{name}`" for name in missing)
    lines.extend(
        [
            "",
            "Tiger-only facts, such as the stored position count, are not here: they need the",
            "database rather than the snapshot.",
            "",
        ]
    )
    return "\n".join(lines)


def _write(path: Path, text: str) -> None:
    """Atomic write so a reader never sees half a sheet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def merge_stats(source: Path, target: Path) -> int:
    """Copy only the Tiger aggregates into ``stats.json``, keeping dark hours and validation."""
    fresh = _read_json(source)
    if not isinstance(fresh, dict):
        print(f"cannot read exported stats: {source}")
        return 2
    current = _read_json(target)
    merged = dict(current) if isinstance(current, dict) else {}
    for key in STATS_KEYS:
        if key in fresh:
            merged[key] = fresh[key]
    _write(target, json.dumps(merged, indent=2) + "\n")
    print(f"merged {', '.join(k for k in STATS_KEYS if k in fresh)} into {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write out/numbers.md and out/numbers.json.")
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument(
        "--merge-stats",
        type=Path,
        default=None,
        metavar="PATH",
        help="merge ais_hourly/sar_weekly/source from PATH into <snapshot-dir>/stats.json and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.merge_stats is not None:
        return merge_stats(args.merge_stats, args.snapshot_dir / "stats.json")

    sheet, missing = collect(args.snapshot_dir, args.validation)
    generated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "generated_at": generated_at,
        "values": sheet.values,
        "lines": sheet.lines,
        "missing": missing,
    }
    _write(args.out_md, _render_markdown(sheet, missing, generated_at))
    _write(args.out_json, json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.out_md} and {args.out_json} ({len(sheet.lines)} numbers)")
    if missing:
        print(f"missing {len(missing)} source(s): {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
