"""The V3 watch screen's physics (issue #86): ships that are dark *now*, and who could meet them.

Two questions, both answered from the same recording:

* An **open silence** is a ship whose last position report is already older than the gap
  threshold, and which was underway when it went quiet. Unlike :mod:`core.kinematics`, there
  is no reappearance to check, so the gap is measured to `now` instead of to a second fix,
  and the evidence can only use what happened up to `now`.
* A **possible contact** is any other vessel that was inside the dark ship's reach area at a
  time when the dark ship could have been there. Reach is a disk growing from the last fix
  while the ship is still missing, or the intersection of that disk and the disk that can
  still reach the next fix (the space-time lens) once the ship reports again. Being in reach
  is not a meeting: the contact's representative ping must also be stopped, offshore and
  outside a routine anchorage for ``could_meet``, which is the gate the real recording's
  alert list needed (see :func:`contact_could_meet`).

Both reuse the kinematics helpers rather than restating their rules: one definition of
"covered", one definition of "this receiver is still hearing other ships", so a silence counted
here and a silence counted by `kinematics.silences` cannot disagree about the same thresholds.

Pure: no files, no clock. `now` is an argument, never `datetime.now()`, so the pipeline can
replay a recording and get the same answers it got live.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .anchorages import context_for
from .geo import KNOT_KM_H, Fix, distance_km
from .kinematics import (
    _DEFAULT_ACTIVITY_BUCKET_MIN,
    _DEFAULT_LOCAL_RADIUS_KM,
    BERTH_SOG_KN,
    ActivityIndex,
    _coverage_floor,
    _fmt_duration,
    _nearby_vessel_counts,
    covered,
    local_activity,
)
from .prism import EPS_KM, in_lens, unwrap_lon
from .tracks import CoverageMinute, Event, Ping

__all__ = [
    "MIN_OFFSHORE_KM",
    "Contact",
    "contact_could_meet",
    "dark_type_allowed",
    "in_reach",
    "offshore_km",
    "open_silences",
    "possible_contacts",
    "reach_km",
]

MIN_OFFSHORE_KM = 5.0
"""A stopped hull closer than this to the coast is port traffic, not a rendezvous.

The same gate ``core.encounters`` applies to visible meetings, reused here so a contact and a
meeting in the same water cannot disagree about what counts as offshore.
"""

KM_PER_DEG = 111.0
"""Planar degrees-to-kilometres factor, matching the approximation ``core.encounters`` uses."""


def dark_type_allowed(ship_type: int | None) -> bool:
    """Whether a hull's AIS type may carry a dark alert at all.

    On the 12 h recording the alert list was dominated by passenger ferries: a long gap's reach
    disk almost always contains some watchlisted hull, so ordinary ferry traffic inherited the
    "went dark near shadow fleet ship" label. Only cargo (70-79) and tanker (80-89) hulls, plus
    hulls whose type is genuinely missing (``None`` or AIS 0 "not available"), are dark
    candidates. Fishing, service, pilot/tug, high-speed craft, pleasure and passenger hulls are
    never dark candidates, whatever their reach disk happens to contain.
    """
    if ship_type is None or ship_type == 0:
        return True
    return 70 <= ship_type <= 89


def offshore_km(lat: float, lon: float, land: BaseGeometry | None) -> float | None:
    """Distance in km from ``(lat, lon)`` to the nearest land, or None without a land geometry.

    ``None`` means "cannot tell", not "ashore": callers apply the offshore gate only when a
    distance exists, so a build without the land file still works. A planar degree distance
    times 111 km is the same approximation the meeting detector already accepts at this
    resolution.
    """
    if land is None or land.is_empty:
        return None
    return land.distance(Point(lon, lat)) * KM_PER_DEG


def contact_could_meet(
    ping: Ping,
    land: BaseGeometry | None = None,
    *,
    stopped_kn: float = 3.0,
    min_offshore_km: float = MIN_OFFSHORE_KM,
) -> bool:
    """Whether one contact's in-reach ping is a plausible meeting with a dark ship.

    Being stopped is not enough. A hull waiting at the quay, or inside a published anchorage
    box, is ordinary port business and was flooding the recording's alert list. The ping must
    also be at least ``min_offshore_km`` from land (unknown land cannot exclude it) and outside
    every routine anchorage in :mod:`core.anchorages`. Ship-to-ship hot spots are deliberately
    not excluded: a stop there is the behaviour the screen exists to surface.
    """
    if ping.sog is None or ping.sog > stopped_kn:
        return False
    if context_for(ping.lat, ping.lon) is not None:
        return False
    away = offshore_km(ping.lat, ping.lon, land)
    return away is None or away >= min_offshore_km


@dataclass(frozen=True)
class Contact:
    """One other vessel that could have met a dark ship, at its closest inside the reach area.

    Names and watchlist categories are deliberately absent: this module only knows the
    recording, and the pipeline joins static data afterwards. `stopped` and `could_meet` look
    redundant but are kept apart because the watch contract exposes both, and a caller may
    later want "was stopped at its closest fix" without changing the reach rule.
    """

    mmsi: int
    lat: float
    lon: float
    t: datetime
    min_dist_km: float
    stopped: bool
    could_meet: bool


def reach_km(cap_kn: float, t0: datetime, t: datetime) -> float:
    """Radius (km) a ship capped at `cap_kn` can have covered between `t0` and `t`.

    Clamped at zero for times before `t0` so the result is monotone non-decreasing: a caller
    that walks `t` forward from the last fix never has to special-case the first step. The
    formula is the contract's `speed_cap_kn x 1.852 x hours(t - last.t)`.
    """
    hours = (t - t0).total_seconds() / 3600.0
    if hours <= 0.0:
        return 0.0
    return max(0.0, cap_kn) * KNOT_KM_H * hours


def in_reach(last: Fix, next: Fix | None, cap_kn: float, lat: float, lon: float, t: datetime) -> bool:
    """Could the dark ship be at `(lat, lon)` at time `t`?

    While the ship is still missing (`next is None`) the reach area is the disk of radius
    `reach_km` around the last fix; before the last fix there is no reach area at all. Once it
    reports again the area is the space-time lens: reachable from `last` at `t` *and* still
    able to reach `next` by `next.t`, which is strictly smaller than the disk. Both tests use
    geodesic distance, so longitudes on either side of the antimeridian are handled natively;
    `unwrap_lon` is applied to the query longitude anyway so callers may pass either
    representation of the same meridian.
    """
    lon = unwrap_lon(lon, last.lon)
    if next is not None:
        return in_lens(last, next, cap_kn, lat, lon, t)
    if t < last.t:
        return False
    return distance_km(last.lat, last.lon, lat, lon) <= reach_km(cap_kn, last.t, t) + EPS_KM


def open_silences(
    tracks: Mapping[int, Sequence[Ping]],
    coverage: Iterable[CoverageMinute],
    now: datetime,
    *,
    min_gap_min: float = 60.0,
    min_sog: float = 2.0,
    activity: ActivityIndex | None = None,
) -> list[Event]:
    """AIS gaps still open at `now`: the ship is missing right now, not merely between fixes.

    The rules are the ones `kinematics.silences` applies, with the second fix replaced by
    `now`: the last ping must be older than `min_gap_min`, the ship must have been underway
    (SOG at or above `min_sog`, and never below the berth floor), and the recorder network must
    have been hearing traffic for every minute of `[last.t, now]` - `covered` globally and,
    when an `activity` index is supplied, `local_activity` within 60 km of the last fix.

    The returned Event has `end=None`: the gap has not closed yet, and the pipeline turns that
    into `status: "ongoing"`. `end` is annotated `datetime` in the shared Event shape, so an
    open event is the one place that annotation is knowingly widened at runtime.
    """
    events: list[Event] = []
    coverage_rows = list(coverage)
    sog_floor = max(min_sog, BERTH_SOG_KN)
    for mmsi, track in tracks.items():
        if not track:
            continue
        last = max(track, key=lambda p: p.t)
        minutes = (now - last.t).total_seconds() / 60.0
        if minutes <= min_gap_min:
            continue
        if last.sog is None or last.sog < sog_floor:
            continue
        if not covered(last.t, now, coverage_rows):
            continue
        local_heard: int | None = None
        if activity is not None:
            if not local_activity(activity, last.lat, last.lon, last.t, now, exclude_mmsi=mmsi):
                continue
            local_heard = min(
                _nearby_vessel_counts(
                    activity,
                    last.lat,
                    last.lon,
                    last.t,
                    now,
                    radius_km=_DEFAULT_LOCAL_RADIUS_KM,
                    bucket_min=_DEFAULT_ACTIVITY_BUCKET_MIN,
                    exclude_mmsi=mmsi,
                )
            )
        # No reappearance point exists, so severity cannot use distance covered: the verified
        # gap itself is worth half the scale, and duration supplies the rest.
        duration_score = min(1.0, minutes / (4.0 * max(min_gap_min, 1.0)))
        severity = round(min(1.0, 0.5 + 0.5 * duration_score), 3)
        if local_heard is None:
            explanation = (
                f"Silent for {_fmt_duration(minutes)} while underway at {last.sog:.0f} kn; "
                "the receiver network kept hearing other ships and it has not reported since."
            )
        else:
            explanation = (
                f"Silent for {_fmt_duration(minutes)} while underway at {last.sog:.0f} kn; "
                f"receivers within {_DEFAULT_LOCAL_RADIUS_KM:.0f} km kept hearing "
                f"{local_heard} other ships and it has not reported since."
            )
        evidence: dict[str, object] = {
            "minutes": round(minutes, 1),
            "last_lat": last.lat,
            "last_lon": last.lon,
            "coverage_min_msgs": _coverage_floor(last.t, now, coverage_rows),
            "ongoing": True,
        }
        if local_heard is not None:
            evidence["local_other_vessels"] = local_heard
        events.append(
            Event(
                kind="silence",
                mmsi=last.mmsi,
                start=last.t,
                # Open gap: the pipeline maps this to status "ongoing".
                end=None,  # type: ignore[arg-type]
                # Mark the map where the ship was last heard, not where it might be now.
                lat=last.lat,
                lon=last.lon,
                severity=severity,
                explanation=explanation,
                evidence=evidence,
            )
        )
    return events


def possible_contacts(
    dark_mmsi: int,
    last: Fix,
    next: Fix | None,
    cap_kn: float,
    t_start: datetime,
    t_end: datetime,
    tracks: Mapping[int, Sequence[Ping]],
    *,
    stopped_kn: float = 3.0,
    max_contacts: int = 20,
    land: BaseGeometry | None = None,
    min_offshore_km: float = MIN_OFFSHORE_KM,
) -> list[Contact]:
    """Other vessels that were inside the dark ship's reach area during `[t_start, t_end]`.

    A ping counts when it is inside `in_reach` at its own timestamp: the reach area moves with
    time, so a ship that was 100 km away while the dark ship had only been missing an hour is
    not a contact even if it is inside the final, larger disk. For each other vessel only the
    ping closest to the dark ship's last fix is kept - that is the position the UI drops on
    the map. `stopped` records whether that representative ping was at or below `stopped_kn`;
    `could_meet` additionally requires it to be offshore and outside a routine anchorage, via
    :func:`contact_could_meet`. A hull that fails those gates is still listed as in reach, with
    `could_meet` false, because the map should show it without claiming a meeting.

    Sorted with potential meetings first, then by distance, then MMSI so repeated runs and
    equal distances are deterministic. `max_contacts` is applied after sorting; zero returns
    an empty list rather than a Python slice surprise.
    """
    if t_end < t_start or max_contacts <= 0:
        return []
    max_radius = reach_km(cap_kn, last.t, t_end)
    best: dict[int, tuple[float, datetime, Ping]] = {}
    for mmsi, track in tracks.items():
        if mmsi == dark_mmsi:
            continue
        for ping in track:
            if ping.t < t_start or ping.t > t_end:
                continue
            dist = distance_km(last.lat, last.lon, ping.lat, ping.lon)
            # The lens is inside the disk from `last`, so this bound rejects most pings with
            # one distance and keeps the exact in_reach test for the rest.
            if dist > max_radius + EPS_KM:
                continue
            if not in_reach(last, next, cap_kn, ping.lat, ping.lon, ping.t):
                continue
            current = best.get(mmsi)
            if current is None or (dist, ping.t) < (current[0], current[1]):
                best[mmsi] = (dist, ping.t, ping)
    contacts = [
        Contact(
            mmsi=mmsi,
            lat=ping.lat,
            lon=ping.lon,
            t=ping.t,
            min_dist_km=round(dist, 3),
            stopped=ping.sog is not None and ping.sog <= stopped_kn,
            could_meet=contact_could_meet(ping, land, stopped_kn=stopped_kn, min_offshore_km=min_offshore_km),
        )
        for mmsi, (dist, _, ping) in best.items()
    ]
    contacts.sort(key=lambda c: (not c.could_meet, c.min_dist_km, c.mmsi))
    return contacts[:max_contacts]
