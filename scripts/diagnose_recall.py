"""Diagnose why in-scope injected gaps miss the silence rule (issue #147).

    ~/.local/bin/uv run python scripts/diagnose_recall.py [--jsonl-dir DIR] [--seed 7]

Replays the seeded run in ``scripts/validate_tracks.py`` by importing it and recording its
``inject_gaps`` calls, so the tracks, the injections and the receiver coverage are the
harness's own: same flags, same order of random draws, no copied logic. For every gap that
starts with the ship at 2 knots or more -- the silence rule's scope -- it asks why no
detector event covered at least half of it, checking in this order:

a. the gap itself is no longer than ``min_gap_min``;
b. ``covered()`` is false, i.e. the receiver heard too little inside the gap;
c. an event does overlap the gap but covers less than half of it;
d. the gap's start ping is not the ping the detector sees as "last";
e. anything else.

It prints a summary count per reason and writes ``docs/recall-misses.md``. Like the
harness, an empty store is not an error: it prints "no data", writes a placeholder report
and exits 0.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_tracks as harness  # noqa: E402

from darkfleet.core import kinematics  # noqa: E402
from darkfleet.core.track_validation import _gap_found  # noqa: E402
from darkfleet.core.tracks import CoverageMinute, Event, Ping  # noqa: E402

OUT_MD = ROOT / "docs" / "recall-misses.md"
OUT_REL = "docs/recall-misses.md"

# The silence rule's scope, as detector_recall measures it: a gap counts only when the ship
# was doing at least this many knots at its start ping. _detection hands the detector the
# same value as ``min_sog``.
UNDERWAY_KN = 2.0

# covered()'s default message floor: a minute below this means the receiver was too quiet
# for the silence rule to blame the ship, so the harness skips the gap for that reason.
MIN_MSGS_PER_MIN = 150

# Checked in this order, so a gap with several problems is counted under the first one.
REASONS: tuple[tuple[str, str], ...] = (
    ("duration", "Gap no longer than min_gap_min"),
    ("coverage", "Receiver coverage below floor"),
    ("partial", "An event covers <50% of the gap"),
    ("mismatch", "Detector's last ping differs"),
    ("other", "Other"),
)

# One sentence each, used in the report when that reason dominates.
GLOSS = {
    "duration": "A duration miss means the cut itself was no longer than the rule's minimum, "
    "so the detector was never supposed to report it",
    "coverage": "A coverage miss means at least one minute of the gap fell below the receiver's "
    "message floor, which the rule reads as a hole in our own listening rather than a dark ship",
    "partial": "A partial-event miss means the detector did fire over the gap but covered less "
    "than half of it, so the event boundaries and the injected boundaries disagree",
    "mismatch": "A last-ping miss means the harness cut from one ping while the detector paired "
    "the gap from a different one, which points at the thinning/dedupe handover, not at the rule",
    "other": "An 'other' miss means no ordered check explained it, so that MMSI needs tracing by hand",
}


@dataclass(frozen=True, slots=True)
class Miss:
    """One in-scope injected gap the silence rule did not report."""

    mmsi: int
    start: datetime
    end: datetime
    minutes: float
    reason: str
    detail: str


def _fmt_ts(t: datetime) -> str:
    """UTC timestamp as the report writes it: minutes are enough to find the gap."""
    return t.strftime("%Y-%m-%d %H:%M") + " UTC"


def _fmt_gap(start: datetime, end: datetime) -> str:
    return f"{_fmt_ts(start)} to {_fmt_ts(end)}"


def _best_overlap(events: Sequence[Event], start: datetime, end: datetime) -> float:
    """Largest fraction of [start, end] that any one detector event covers."""
    span = (end - start).total_seconds()
    if span <= 0:
        return 0.0
    best = 0.0
    for ev in events:
        overlap = (min(end, ev.end) - max(start, ev.start)).total_seconds()
        if overlap > 0:
            best = max(best, overlap / span)
    return best


def _coverage_detail(
    start: datetime, end: datetime, coverage: Sequence[CoverageMinute]
) -> tuple[int, int, int]:
    """Worst minute's message count and how many minutes sit below the floor.

    The minute walk mirrors ``kinematics.covered`` (both end minutes included, absent means
    zero) and reuses its own counter so duplicate minute rows are summed the same way; only
    the reporting is new, not the decision.
    """
    counts = kinematics._minute_counts(coverage)
    minute = start.replace(second=0, microsecond=0)
    last = end.replace(second=0, microsecond=0)
    worst: int | None = None
    below = 0
    total = 0
    while minute <= last:
        n = counts.get(minute, 0)
        worst = n if worst is None else min(worst, n)
        below += int(n < MIN_MSGS_PER_MIN)
        total += 1
        minute += timedelta(minutes=1)
    return (0 if worst is None else worst), below, total


def _pair_last(ordered: Sequence[Ping], start: datetime) -> Ping | None:
    """The ping the detector pairs with the gap's first missing report.

    ``silences`` walks consecutive retained pings. The gap's start is the last retained ping
    before the cut, so the pair that spans it must start exactly there; anything else means
    the track the detector sees and the track the harness cut from disagree.
    """
    for last, nxt in pairwise(ordered):
        if last.t <= start < nxt.t:
            return last
    return None


def _classify(
    ordered: Sequence[Ping],
    events: Sequence[Event],
    coverage: Sequence[CoverageMinute],
    start: datetime,
    end: datetime,
    sog: float,
    min_gap_min: float,
) -> Miss:
    """First reason, in the documented order, that the silence rule stayed quiet."""
    minutes = (end - start).total_seconds() / 60.0

    def miss(reason: str, detail: str) -> Miss:
        return Miss(
            mmsi=ordered[0].mmsi,
            start=start,
            end=end,
            minutes=minutes,
            reason=reason,
            detail=detail,
        )

    if minutes <= min_gap_min:
        return miss("duration", f"{minutes:.0f} min is not longer than {min_gap_min:g} min")

    if not kinematics.covered(start, end, coverage):
        worst, below, total = _coverage_detail(start, end, coverage)
        return miss(
            "coverage",
            f"worst minute {worst} msgs (floor {MIN_MSGS_PER_MIN}); "
            f"{below} of {total} minutes below the floor",
        )

    fraction = _best_overlap(events, start, end)
    if fraction > 0:
        return miss("partial", f"best overlapping event covers {100.0 * fraction:.0f}% of the gap")

    last = _pair_last(ordered, start)
    if last is None:
        return miss("mismatch", "no detector pair spans the gap start")
    if last.t != start:
        return miss("mismatch", f"detector's last ping is {_fmt_ts(last.t)}, not the gap start")
    if last.sog != sog:
        return miss("mismatch", f"detector's last ping reports SOG {last.sog}, the cut used {sog}")

    return miss("other", f"no event and the detector's last ping matches (SOG {sog:g} kn)")


def _diagnose(
    tracks: Sequence[tuple[Sequence[Ping], Sequence[tuple[datetime, datetime]]]],
    coverage: Sequence[CoverageMinute],
    args: argparse.Namespace,
    silences: Callable[..., list[Event]],
) -> tuple[list[Miss], int, int]:
    """Classify every missed in-scope gap, returning misses plus scope/found counts."""
    misses: list[Miss] = []
    in_scope = 0
    found = 0
    for pings, gaps in tracks:
        if not gaps:
            continue
        events = silences(list(pings), coverage, min_gap_min=args.min_gap_min, min_sog=2.0)
        speed_at = {p.t: p.sog for p in pings}
        ordered = sorted(pings, key=lambda p: p.t)
        for start, end in gaps:
            sog = speed_at.get(start)
            # Same scope test as detector_recall: a gap with no SOG at its start ping is
            # outside what the rule claims, so it is not a recall miss at all.
            if sog is None or sog < UNDERWAY_KN:
                continue
            in_scope += 1
            if _gap_found(events, start, end):
                found += 1
                continue
            misses.append(_classify(ordered, events, coverage, start, end, sog, args.min_gap_min))
    return misses, in_scope, found


def _print_misses(misses: Sequence[Miss]) -> None:
    labels = dict(REASONS)
    for m in misses:
        print(
            f"miss {m.mmsi} {_fmt_gap(m.start, m.end)} ({m.minutes:.0f} min): {labels[m.reason]}: {m.detail}"
        )


def _print_summary(misses: Sequence[Miss], in_scope: int, found: int) -> None:
    counts = Counter(m.reason for m in misses)
    print(f"in-scope gaps {in_scope}; found {found}; missed {len(misses)}")
    width = max(len(label) for _, label in REASONS)
    for key, label in REASONS:
        print(f"  {label:<{width}}  {counts.get(key, 0):>4}")


def _example(misses: Sequence[Miss], reason: str) -> str:
    for m in misses:
        if m.reason == reason:
            return f"`{m.mmsi}`, {_fmt_gap(m.start, m.end)}"
    return "-"


def _interpret(misses: Sequence[Miss], in_scope: int, found: int, source: str) -> str:
    """Exactly three plain sentences explaining what the counts mean."""
    missed = len(misses)
    if in_scope == 0:
        return (
            f"The run produced no in-scope injected gaps from {source}, so there is nothing to "
            "diagnose yet. Recall inside the rule's scope is undefined rather than zero here. "
            "Re-run once the recorder has a full day of AIS traffic in the box."
        )
    if missed == 0:
        return (
            f"All {in_scope} in-scope injected gaps were found, so the rule's stated scope matches "
            "what it actually does on this recording. No gap was lost to duration, coverage, a "
            "partial event or a thinning mismatch. Re-run after more recording to see whether that "
            "holds when the feed is busier."
        )
    counts = Counter(m.reason for m in misses)
    top_key, top_n = counts.most_common(1)[0]
    top_label = dict(REASONS)[top_key].lower()
    return (
        f"Of {in_scope} in-scope injected gaps, {found} were found and {missed} were missed, and "
        f"{top_label} accounts for {top_n} of the misses. {GLOSS[top_key]}. Coverage and duration "
        "misses are the rule working as designed, while a partial-event, last-ping or other miss "
        "means the harness and the detector disagree and is worth fixing."
    )


def _render(misses: Sequence[Miss], in_scope: int, found: int, results: dict[str, object]) -> str:
    counts = Counter(m.reason for m in misses)
    rows = [[label, str(counts.get(key, 0)), _example(misses, key)] for key, label in REASONS]
    lines = [
        "# Why the silence rule misses in-scope injected gaps",
        "",
        f"Generated: {results['generated_at']}",
        f"Seed: {results['seed']}",
        f"Source: {results['source']}",
        "",
        "An in-scope gap is one whose start ping reports at least 2 knots, which is the only "
        "case the silence rule claims. Each missed gap is counted under the first reason that "
        "explains it, checked in the order of the table.",
        "",
        f"In-scope gaps: {in_scope}; found: {found}; missed: {len(misses)}.",
        "",
        *harness._table(["Reason", "Count", "Example MMSI and gap (UTC)"], rows),
        "",
        "## Reading",
        "",
        _interpret(misses, in_scope, found, str(results["source"])),
        "",
        "Data: our own AIS recorder. No Global Fishing Watch data is used in this report.",
    ]
    return "\n".join(lines) + "\n"


def _render_placeholder(reason: str) -> str:
    lines = [
        "# Why the silence rule misses in-scope injected gaps",
        "",
        "Placeholder. `scripts/diagnose_recall.py` regenerates this file from a real run.",
        "",
        f"Skipped: {reason}.",
        "",
        "Run it against the store, or against raw aisstream files:",
        "",
        "    ~/.local/bin/uv run python scripts/diagnose_recall.py",
        "    ~/.local/bin/uv run python scripts/diagnose_recall.py --jsonl-dir <dir>",
        "",
        "Data: our own AIS recorder. No Global Fishing Watch data is used in this report.",
    ]
    return "\n".join(lines) + "\n"


def _write(text: str) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text)
    print(f"wrote {OUT_REL}")


def main() -> int:
    """Load the harness's data, replay its seeded run, and report the misses."""
    args = harness._parse_args()
    tracks, n_pings, coverage, source = harness._load(args)
    if not tracks:
        _write(_render_placeholder("the store has no AIS positions"))
        print("no data")
        return 0

    # Record the harness's own injections instead of re-deriving them: the wrapper returns
    # the real result unchanged, so the RNG stream and every downstream metric are untouched.
    calls: list[tuple[list[Ping], list[tuple[datetime, datetime]]]] = []
    real_inject = harness.inject_gaps

    def recording_inject(track, rng, n):
        kept, gaps = real_inject(track, rng, n)
        calls.append((list(kept), list(gaps)))
        return kept, gaps

    harness.inject_gaps = recording_inject
    try:
        results = harness._run(args, tracks, n_pings, coverage, source)
    finally:
        harness.inject_gaps = real_inject

    detection = results.get("detection", {})
    if detection.get("status") != "ok":
        reason = str(detection.get("reason", "the silence detector or coverage series is missing"))
        _write(_render_placeholder(reason))
        print(f"skipped: {reason}")
        return 0

    silences = harness._kinematics("silences")
    if silences is None:
        _write(_render_placeholder("core.kinematics is not in this build"))
        print("skipped: core.kinematics is not in this build")
        return 0

    misses, in_scope, found = _diagnose(calls, coverage, args, silences)
    reference = (detection.get("found_underway"), detection.get("injected_underway"))
    if reference != (found, in_scope):
        print(
            f"warning: replay counts ({found} of {in_scope}) disagree with the harness {reference}; "
            "the reasons below are still per-gap but the totals moved"
        )

    _print_misses(misses)
    _print_summary(misses, in_scope, found)
    _write(_render(misses, in_scope, found, results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
