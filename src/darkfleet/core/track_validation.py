"""Ground-truth checks for the Baltic engine on our own AIS tracks (issue #74).

The engine claims four things: our space-time prism contains a ship's true path, the
Brownian bridge ranks the true path above displaced alternatives, our silence detector
finds real gaps, and our jump/clone rules stay quiet on clean tracks. This module
measures all four by hiding part of a track we already recorded and asking the engine to
recover it. The hidden fixes are ground truth because the recorder saw them; no external
dataset is needed.

Everything here is pure (no I/O). Every random choice takes an explicit
``numpy.random.Generator`` so a published number is reproducible from a seed, and the
speed cap is a caller-supplied mapping so the same cases can be re-scored after the
speed table changes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np

from .bridge import bridge_logpdf, paired_auc
from .geo import Fix
from .prism import in_footprint, in_lens
from .tracks import CoverageMinute, Event, Ping
from .validation import offset_point, random_offsets, wilson_interval

__all__ = [
    "MaskCase",
    "bridge_auc",
    "containment",
    "detector_recall",
    "false_positive_rate",
    "inject_gaps",
    "mask_segments",
]

# A hidden window's score must not depend on how often the transponder reported, so the
# bridge test reads at most this many evenly spaced truth fixes per case. Containment
# still uses every hidden fix: a missed corner of the prism is exactly what it hunts for.
_MAX_SCORED_TRUTHS = 16

# An injected gap counts as found when a detector event covers at least this fraction of
# it. Half is deliberately loose: a detector that starts and ends a few pings inside the
# real gap is still a hit, while one that only clips the edge is not.
_FOUND_FRACTION = 0.5

# Injected gaps target 1-6 hours because that straddles the 60-minute silence default:
# shorter injections would be invisible to the detector by design, longer ones would
# remove most of a day's track and leave too few clean pings around them.
_GAP_MIN_H = 1.0
_GAP_MAX_H = 6.0


@dataclass(frozen=True, slots=True)
class MaskCase:
    """One hidden window: the two surviving fixes and the fixes that were concealed.

    ``a`` is the last ping before the window and ``b`` the first one after it, so the
    engine sees exactly what it would see after a real transponder outage. ``truths`` are
    the readings the recorder actually received inside that outage, in time order.
    ``vessel_class`` keys the speed cap used when scoring the case.
    """

    a: Fix
    b: Fix
    truths: list[Fix]
    vessel_class: str


class ClassedTrack(Protocol):
    """A track that knows its own speed class.

    ``Ping`` carries no ship type (that lives in a static message), so the caller that
    joined positions to vessel info passes it alongside the pings. A plain sequence of
    ``Ping`` is also accepted and is scored as class ``"default"``.
    """

    vessel_class: str
    pings: Sequence[Ping]


Detector = Callable[[Sequence[Ping]], list[Event]]
SilenceDetector = Callable[[Sequence[Ping], Sequence[CoverageMinute]], list[Event]]


def _hours(t0: datetime, t1: datetime) -> float:
    return (t1 - t0).total_seconds() / 3600.0


def _fix(p: Ping) -> Fix:
    return Fix(p.lat, p.lon, p.t)


def _sorted_unique(pings: Sequence[Ping]) -> list[Ping]:
    """Time-ordered pings with duplicate timestamps collapsed.

    A masked window is defined by strictly increasing times; two reports with the same
    timestamp would make a window's duration ambiguous. Sorting also means callers may
    hand over pings in arrival order, which is how the recorder writes them.
    """
    ordered = sorted(pings, key=lambda p: p.t)
    out: list[Ping] = []
    for p in ordered:
        if not out or p.t != out[-1].t:
            out.append(p)
    return out


def _underway_runs(underway: Sequence[bool]) -> list[tuple[int, int]]:
    """Inclusive index ranges of consecutive underway pings long enough to hide a window."""
    runs: list[tuple[int, int]] = []
    i, n = 0, len(underway)
    while i < n:
        if not underway[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and underway[j + 1]:
            j += 1
        if j - i >= 2:  # need a fix before, at least one truth, and a fix after
            runs.append((i, j))
        i = j + 1
    return runs


def _overlaps(used: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    """True if the closed index range [start, end] touches an already-masked range."""
    return any(not (end < lo or start > hi) for lo, hi in used)


def _track_parts(track: Sequence[Ping] | ClassedTrack) -> tuple[list[Ping], str]:
    """Split a track into pings and a vessel class, accepting both input shapes."""
    pings = getattr(track, "pings", None)
    if pings is not None:
        return list(pings), str(getattr(track, "vessel_class", "default"))
    return list(track), "default"


def mask_segments(
    track: Sequence[Ping] | ClassedTrack,
    rng: np.random.Generator,
    *,
    min_h: float = 1,
    max_h: float = 12,
    min_sog: float = 2.0,
) -> list[MaskCase]:
    """Hide random 1-12 hour underway stretches of one track.

    Only stretches where every report says the ship was moving are eligible: a ship at
    anchor is not "dark", and masking a berthed vessel would measure the wrong thing.
    Windows never share a ping and are shortened to whole ping intervals, so every truth
    is bracketed by the two surviving fixes and every case is a gap the engine could
    really have been asked about. The duration is drawn in hours rather than in pings so
    a densely reporting ship and a sparse one yield comparable cases.
    """
    if min_h <= 0 or max_h < min_h:
        raise ValueError("mask_segments needs 0 < min_h <= max_h")
    if max_h * 3600 <= 0:
        raise ValueError("mask_segments needs a positive time range")
    pings, vessel_class = _track_parts(track)
    pings = _sorted_unique(pings)
    if len(pings) < 3:
        return []
    underway = [p.sog is not None and p.sog >= min_sog for p in pings]
    runs = _underway_runs(underway)
    if not runs:
        return []

    used: list[tuple[int, int]] = []
    cases: list[MaskCase] = []
    # Failures are mostly overlaps near the ends of short runs; a handful of extra draws
    # per run is enough to fill long tracks without turning a thin track into a loop.
    attempts = 12 * len(runs) + 12
    for _ in range(attempts):
        i0, i1 = runs[int(rng.integers(0, len(runs)))]
        start = int(rng.integers(i0, i1 - 1))
        target_h = float(rng.uniform(min_h, max_h))
        end = start + 1
        while end <= i1 and _hours(pings[start].t, pings[end].t) < target_h:
            end += 1
        if end > i1 or end - start < 2:
            continue
        span_h = _hours(pings[start].t, pings[end].t)
        # A coverage gap inside the run can make the next ping land beyond max_h; such a
        # window is not a 1-12 h mask and would double-count a real outage.
        if span_h > max_h + 1e-9:
            continue
        if _overlaps(used, start, end):
            continue
        used.append((start, end))
        cases.append(
            MaskCase(
                a=_fix(pings[start]),
                b=_fix(pings[end]),
                truths=[_fix(p) for p in pings[start + 1 : end]],
                vessel_class=vessel_class,
            )
        )
    cases.sort(key=lambda c: c.a.t)
    return cases


def _cap(caps: dict[str, float], vessel_class: str) -> float:
    """Speed cap for a class, falling back to ``"default"`` when the class is unknown.

    The fallback matters because a static message may never arrive for a vessel; using
    the generic cap is still a physically meaningful bound, while silently skipping the
    case would bias the sample towards well-documented ships.
    """
    if vessel_class in caps:
        return float(caps[vessel_class])
    if "default" in caps:
        return float(caps["default"])
    raise KeyError(f"no speed cap for vessel class {vessel_class!r} and no 'default' key")


def containment(
    cases: Sequence[MaskCase],
    cap_kn_by_class: dict[str, float],
) -> dict[str, Any]:
    """Share of masked windows whose every hidden fix the prism can explain.

    A case is a hit only if *all* of its hidden fixes are inside the footprint and, more
    strictly, inside the lens at each fix's own timestamp. Requiring the whole hidden
    path rather than one point catches a prism that is right at the ends and pinched in
    the middle, which is the failure mode the lens geometry is most likely to have.
    ``rate`` reports the stricter lens number and ``wilson_ci`` its 95% interval.
    """
    n = len(cases)
    inside_footprint = 0
    inside_lens = 0
    for case in cases:
        if not case.truths:
            continue
        v = _cap(cap_kn_by_class, case.vessel_class)
        if all(in_footprint(case.a, case.b, v, t.lat, t.lon) for t in case.truths):
            inside_footprint += 1
        if all(in_lens(case.a, case.b, v, t.lat, t.lon, t.t) for t in case.truths):
            inside_lens += 1
    rate = inside_lens / n if n else float("nan")
    lo, hi = wilson_interval(inside_lens, n)
    return {
        "n": n,
        "inside_footprint": inside_footprint,
        "inside_lens_at_truth_time": inside_lens,
        "rate": rate,
        "wilson_ci": [lo, hi],
    }


def _scored_truths(truths: Sequence[Fix]) -> list[Fix]:
    """Evenly spaced subset of hidden fixes used for bridge scoring (see module note)."""
    if len(truths) <= _MAX_SCORED_TRUTHS:
        return list(truths)
    idx = np.linspace(0, len(truths) - 1, _MAX_SCORED_TRUTHS).round().astype(int)
    return [truths[int(i)] for i in idx]


def bridge_auc(
    cases: Sequence[MaskCase],
    sigma: float,
    rng: np.random.Generator,
    n_decoys: int = 20,
    min_km: float = 20,
    max_km: float = 80,
) -> dict[str, Any]:
    """AUC of hidden-path bridge scores against paths shifted 20-80 km.

    Each case contributes one paired comparison: the mean log-density of its hidden fixes
    against the mean log-density of the same fixes moved by one random offset. Using one
    offset per decoy keeps the sham a coherent displaced path rather than a cloud of
    points, and pairing by case means the bootstrap resamples cases, the independent
    units. ``ci`` is the percentile bootstrap interval from ``core.bridge.paired_auc``.
    """
    if sigma <= 0:
        raise ValueError("bridge_auc needs a positive sigma")
    if n_decoys < 1:
        raise ValueError("bridge_auc needs at least one decoy")
    if max_km <= min_km:
        raise ValueError("bridge_auc needs max_km > min_km")

    truth_scores: list[float] = []
    decoy_scores: list[list[float]] = []
    for case in cases:
        pts = _scored_truths(case.truths)
        if not pts:
            continue
        truth = [bridge_logpdf(case.a, case.b, sigma, p.lat, p.lon, p.t) for p in pts]
        truth_scores.append(float(np.mean(truth)))
        row: list[float] = []
        for _ in range(n_decoys):
            dist, bearing = random_offsets(rng, 1, lo_km=min_km, hi_km=max_km)
            shuffled = []
            for p in pts:
                lat, lon = offset_point(p.lat, p.lon, float(dist[0]), float(bearing[0]))
                shuffled.append(bridge_logpdf(case.a, case.b, sigma, lat, lon, p.t))
            row.append(float(np.mean(shuffled)))
        decoy_scores.append(row)

    n = len(truth_scores)
    truth_arr = np.asarray(truth_scores, dtype=float)
    decoy_arr = np.asarray(decoy_scores, dtype=float).reshape(n, n_decoys)
    auc, lo, hi = paired_auc(truth_arr, decoy_arr, rng)
    return {
        "auc": auc,
        "ci": [lo, hi],
        "n_cases": n,
        "n_decoys": n_decoys,
        "min_km": min_km,
        "max_km": max_km,
    }


def inject_gaps(
    track: Sequence[Ping] | ClassedTrack,
    rng: np.random.Generator,
    n: int,
) -> tuple[list[Ping], list[tuple[datetime, datetime]]]:
    """Remove n random stretches from a track and return the survivors and the gaps.

    Each stretch removes whole pings between two surviving fixes, so the returned gap is
    exactly the interval a detector will see: from the last ping it still has before the
    cut to the first one after. Gaps never touch the first or last ping and never share a
    ping, and each targets 1-6 hours so it is long enough to matter to a 60-minute
    silence rule. If the track is too short for ``n`` disjoint stretches, fewer are
    returned rather than overlapping ones, which would corrupt the recall count.
    """
    raw, _ = _track_parts(track)
    pings = _sorted_unique(raw)
    total = len(pings)
    if n <= 0 or total < 4:
        return pings, []

    # Reserved index ranges include the surviving fixes on both sides, so the next gap
    # cannot start at the same ping that closes this one.
    used: list[tuple[int, int]] = []
    gaps: list[tuple[datetime, datetime]] = []
    attempts = 40 * n
    for _ in range(attempts):
        if len(gaps) >= n:
            break
        start = int(rng.integers(1, total - 2))
        target_h = float(rng.uniform(_GAP_MIN_H, _GAP_MAX_H))
        end = start + 1
        while end < total and _hours(pings[start].t, pings[end].t) < target_h:
            end += 1
        if end >= total:
            continue
        if _overlaps(used, start - 1, end):
            continue
        used.append((start - 1, end))
        gaps.append((pings[start - 1].t, pings[end].t))

    gaps.sort()
    removed = {i for lo, hi in used for i in range(lo + 1, hi)}
    kept = [p for i, p in enumerate(pings) if i not in removed]
    return kept, gaps


def _gap_found(events: Sequence[Event], start: datetime, end: datetime) -> bool:
    """True if one detector event covers at least half of the injected interval."""
    span = (end - start).total_seconds()
    if span <= 0:
        return False
    for ev in events:
        overlap = (min(end, ev.end) - max(start, ev.start)).total_seconds()
        if overlap > 0 and overlap / span >= _FOUND_FRACTION:
            return True
    return False


def detector_recall(
    tracks: Sequence[tuple[Sequence[Ping], Sequence[tuple[datetime, datetime]]]],
    coverage: Sequence[CoverageMinute],
    detector: SilenceDetector,
    underway_kn: float = 2.0,
) -> dict[str, Any]:
    """Recall of a gap detector on tracks whose gaps we injected ourselves.

    ``tracks`` pairs each survivor track with the gaps that were cut out of it; the pair
    is the only way to tell an injection from a real outage, because the survivor alone
    looks exactly like a ship that went dark. The same ``coverage`` series is passed to
    every track: it describes the receiver, not the vessel.
    """
    injected = 0
    found = 0
    in_scope = 0
    found_in_scope = 0
    n_tracks = 0
    for pings, gaps in tracks:
        n_tracks += 1
        if not gaps:
            continue
        events = detector(list(pings), coverage)
        speed_at = {p.t: p.sog for p in pings}
        for start, end in gaps:
            injected += 1
            hit = _gap_found(events, start, end)
            if hit:
                found += 1
            # The silence rule only claims gaps that begin while the ship is underway; a
            # moored ship going quiet is normal. Recall inside that scope is the fair number.
            sog = speed_at.get(start)
            if sog is not None and sog >= underway_kn:
                in_scope += 1
                if hit:
                    found_in_scope += 1
    recall = found / injected if injected else float("nan")
    return {
        "tracks": n_tracks,
        "injected": injected,
        "found": found,
        "recall": recall,
        "underway_kn": underway_kn,
        "injected_underway": in_scope,
        "found_underway": found_in_scope,
        "recall_underway": found_in_scope / in_scope if in_scope else float("nan"),
    }


def false_positive_rate(
    clean_tracks: Sequence[Sequence[Ping]],
    detector: Detector,
) -> dict[str, Any]:
    """Events per vessel-day that a consistency rule fires on tracks with no injections.

    The denominator is the summed time span of every track in days, which is the
    vessel-days actually observed. A track too short to have a positive span adds no
    denominator; when no track has one the rate is nan rather than zero, because "no
    exposure" is not "no false positives". ``detector`` receives one track at a time and
    returns its events (jump and clone rules, typically).
    """
    n_tracks = 0
    events = 0
    vessel_days = 0.0
    for pings in clean_tracks:
        ordered = _sorted_unique(pings)
        n_tracks += 1
        events += len(detector(ordered))
        if len(ordered) >= 2:
            span = _hours(ordered[0].t, ordered[-1].t) / 24.0
            if span > 0:
                vessel_days += span
    per_vessel_day = events / vessel_days if vessel_days > 0 else float("nan")
    return {
        "tracks": n_tracks,
        "events": events,
        "per_vessel_day": per_vessel_day,
        "vessel_days": vessel_days,
    }
