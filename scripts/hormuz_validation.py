"""Validate the Hormuz dark-share trend with a control area, event study and coverage check.

    uv run python scripts/hormuz_validation.py \
        --treated data/snapshot/hormuz_unseen_long.json \
        --control data/snapshot/gulf_of_oman_unseen_long.json \
        --events data/hormuz_events.json

Reads the two radar-vs-AIS daily series (same Global Fishing Watch SAR source), asks what
changed around each event in Hormuz relative to the open Gulf of Oman control box, and
writes ``docs/hormuz-validation.md`` plus ``data/snapshot/hormuz_validation.json`` for the
UI. The wording is deliberately descriptive: a change "coincides with" an event, it is
not claimed to be caused by it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from darkfleet.core.event_study import (
    DEFAULT_MIN_SEEN,
    DEFAULT_N_BOOT,
    DEFAULT_SEED,
    DEFAULT_WINDOW_DAYS,
    event_study,
    monthly_coverage,
    regime_comparison,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TREATED = ROOT / "data" / "snapshot" / "hormuz_unseen_long.json"
DEFAULT_CONTROL = ROOT / "data" / "snapshot" / "gulf_of_oman_unseen_long.json"
DEFAULT_EVENTS = ROOT / "data" / "hormuz_events.json"
DEFAULT_OUT_JSON = ROOT / "data" / "snapshot" / "hormuz_validation.json"
DEFAULT_OUT_MD = ROOT / "docs" / "hormuz-validation.md"

DEFAULT_BASELINE = ("2025-01-01", "2025-12-31")
DEFAULT_CURRENT = ("2026-03-01", "2026-06-30")

SOURCE = "Global Fishing Watch SAR vessel detections (Sentinel-1)"
METHOD = (
    "For each event we pool the dark share (unmatched radar detections divided by all "
    "detections) over the {window_days} full days before the event and the {window_days} "
    "days after it, in Hormuz and in an open Gulf of Oman control box fetched from the same "
    "SAR swaths. Days with fewer than {min_seen} detections are dropped, and the "
    "difference-in-differences removes changes shared by both areas; its 95% interval is a "
    "{n_boot:,}-resample day-block bootstrap with seed {seed}. A change is reported as "
    "coinciding with an event only when that interval excludes zero; this is a descriptive "
    "comparison, not a causal estimate."
)
CAVEATS = [
    "Radar misses vessels under ~25 m",
    "'Dark' means no AIS match, not wrongdoing",
    "Coincides with, does not establish cause",
    "Control is open Gulf of Oman water (57.6-59.8E, 23.6-25.2N) on the same SAR swaths",
]


def _fmt_share(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _fmt_signed(value: float | None) -> str:
    return "—" if value is None else f"{value:+.3f}"


def _fmt_interval(point: float | None, interval: Sequence[float] | None) -> str:
    """Point estimate with its interval, e.g. ``+0.450 (+0.432 to +0.468)``."""
    if point is None:
        return "—"
    if interval is None:
        return _fmt_signed(point)
    return f"{point:+.3f} ({interval[0]:+.3f} to {interval[1]:+.3f})"


def _period_label(start: str, end: str) -> str:
    return start if start == end else f"{start} to {end}"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _daily(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return list(payload.get("daily") or [])
    if isinstance(payload, list):
        return list(payload)
    raise ValueError("series file must be an object with 'daily' or an array of days")


def _events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return list(payload.get("events") or [])
    if isinstance(payload, list):
        return list(payload)
    raise ValueError("events file must be an array or an object with 'events'")


def build_validation(
    treated: Sequence[dict[str, Any]],
    control: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    *,
    treated_region: str = "HORMUZ",
    control_region: str = "GULF_OF_OMAN_CONTROL",
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_seen: int = DEFAULT_MIN_SEEN,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    baseline: Sequence[str] = DEFAULT_BASELINE,
    current: Sequence[str] = DEFAULT_CURRENT,
) -> dict[str, Any]:
    """Shape the validation payload from already-loaded daily series and events."""
    event_results = event_study(
        treated,
        control,
        events,
        window_days=window_days,
        min_seen=min_seen,
        n_boot=n_boot,
        seed=seed,
    )
    regime = regime_comparison(
        treated,
        control,
        baseline,
        current,
        min_seen=min_seen,
        n_boot=n_boot,
        seed=seed,
    )
    return {
        "generated": date.today().isoformat(),
        "regions": {"treated": treated_region, "control": control_region},
        "window_days": window_days,
        "min_seen": min_seen,
        "n_boot": n_boot,
        "seed": seed,
        "events": event_results,
        "regime": regime,
        "coverage": {
            "treated": monthly_coverage(treated, min_seen=min_seen),
            "control": monthly_coverage(control, min_seen=min_seen),
        },
        "method": METHOD.format(
            window_days=window_days,
            min_seen=min_seen,
            n_boot=n_boot,
            seed=seed,
        ),
        "source": SOURCE,
        "caveats": list(CAVEATS),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    """Render the short human report from the validation payload."""
    regions = payload["regions"]
    treated_label = "Hormuz" if regions["treated"] == "HORMUZ" else regions["treated"]
    control_label = (
        "Control (Gulf of Oman)" if regions["control"] == "GULF_OF_OMAN_CONTROL" else regions["control"]
    )

    lines: list[str] = [
        "# Hormuz radar validation",
        "",
        f"Generated: {payload['generated']}",
        "",
        "## Method",
        "",
        payload["method"],
        "",
        "## Event study",
        "",
        "| Date | Event | Hormuz pre → post | Control pre → post | DiD (95% CI) | Verdict |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for event in payload["events"]:
        treated = event["treated"]
        control = event["control"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _period_label(event["date_start"], event["date_end"]),
                    event["title"] or "(untitled)",
                    f"{_fmt_share(treated['pre']['dark_share'])} → "
                    f"{_fmt_share(treated['post']['dark_share'])}",
                    f"{_fmt_share(control['pre']['dark_share'])} → "
                    f"{_fmt_share(control['post']['dark_share'])}",
                    _fmt_interval(event["did"], event["did_ci"]),
                    event["verdict"],
                ]
            )
            + " |"
        )
    if not payload["events"]:
        lines.append("| — | no events in the events file | — | — | — | — |")

    regime = payload["regime"]
    lines += [
        "",
        "## Overall regime",
        "",
        f"Baseline {regime['baseline']['start']} to {regime['baseline']['end']} against "
        f"{regime['current']['start']} to {regime['current']['end']}.",
        "",
        "| Area | Baseline → current | Change |",
        "| --- | --- | --- |",
        f"| {treated_label} | {_fmt_share(regime['treated']['baseline']['dark_share'])} → "
        f"{_fmt_share(regime['treated']['current']['dark_share'])} | "
        f"{_fmt_signed(regime['treated']['change'])} |",
        f"| {control_label} | {_fmt_share(regime['control']['baseline']['dark_share'])} → "
        f"{_fmt_share(regime['control']['current']['dark_share'])} | "
        f"{_fmt_signed(regime['control']['change'])} |",
        f"| DiD (95% CI) | {_fmt_interval(regime['did'], regime['did_ci'])} | {regime['verdict']} |",
        "",
        "## Coverage",
        "",
        "Days used are days with at least "
        f"{payload['min_seen']} radar detections (a partial satellite pass is left out).",
        "",
        f"| Month | {treated_label} days used | {treated_label} mean detections/day | "
        f"{control_label} days used | {control_label} mean detections/day |",
        "| --- | --- | --- | --- | --- |",
    ]

    treated_months = {row["month"]: row for row in payload["coverage"]["treated"]}
    control_months = {row["month"]: row for row in payload["coverage"]["control"]}
    for month in sorted(set(treated_months) | set(control_months)):
        t = treated_months.get(month)
        c = control_months.get(month)
        lines.append(
            "| "
            + " | ".join(
                [
                    month,
                    "—" if t is None else str(t["days_used"]),
                    "—" if t is None or t["mean_seen_per_day"] is None else f"{t['mean_seen_per_day']:.1f}",
                    "—" if c is None else str(c["days_used"]),
                    "—" if c is None or c["mean_seen_per_day"] is None else f"{c['mean_seen_per_day']:.1f}",
                ]
            )
            + " |"
        )

    lines += ["", "## Caveats", ""]
    lines += [f"- {caveat}" for caveat in payload["caveats"]]
    lines += ["", f"Source: {payload['source']}. Powered by Global Fishing Watch.", ""]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the Hormuz radar trend against a control area.")
    parser.add_argument("--treated", type=Path, default=DEFAULT_TREATED, help="Hormuz daily series JSON")
    parser.add_argument("--control", type=Path, default=DEFAULT_CONTROL, help="control daily series JSON")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS, help="events JSON array")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_JSON, help="validation JSON for the UI")
    parser.add_argument("--md", type=Path, default=DEFAULT_OUT_MD, help="markdown report path")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS, help="days on each side")
    parser.add_argument("--min-seen", type=int, default=DEFAULT_MIN_SEEN, help="minimum detections/day")
    parser.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT, help="bootstrap resamples")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="bootstrap seed")
    parser.add_argument("--baseline-start", default=DEFAULT_BASELINE[0], help="regime baseline start")
    parser.add_argument("--baseline-end", default=DEFAULT_BASELINE[1], help="regime baseline end")
    parser.add_argument("--current-start", default=DEFAULT_CURRENT[0], help="regime current start")
    parser.add_argument("--current-end", default=DEFAULT_CURRENT[1], help="regime current end")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load inputs, build the event study and coverage table, write both outputs."""
    args = build_parser().parse_args(argv)
    try:
        treated_payload = _load_json(args.treated)
        control_payload = _load_json(args.control)
        treated = _daily(treated_payload)
        control = _daily(control_payload)
        events = _events(_load_json(args.events))
        treated_region = (
            str(treated_payload.get("region", "HORMUZ")) if isinstance(treated_payload, dict) else "HORMUZ"
        )
        control_region = (
            str(control_payload.get("region", "GULF_OF_OMAN_CONTROL"))
            if isinstance(control_payload, dict)
            else "GULF_OF_OMAN_CONTROL"
        )
        payload = build_validation(
            treated,
            control,
            events,
            treated_region=treated_region,
            control_region=control_region,
            window_days=args.window_days,
            min_seen=args.min_seen,
            n_boot=args.n_boot,
            seed=args.seed,
            baseline=(args.baseline_start, args.baseline_end),
            current=(args.current_start, args.current_end),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"hormuz validation failed: {exc}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.md.write_text(render_markdown(payload), encoding="utf-8")

    counts = {event["verdict"]: 0 for event in payload["events"]}
    for event in payload["events"]:
        counts[event["verdict"]] = counts.get(event["verdict"], 0) + 1
    print(
        f"treated={treated_region} control={control_region} events={len(payload['events'])} "
        f"verdicts={counts} regime={payload['regime']['verdict']}"
    )
    print(f"wrote {args.out} and {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
