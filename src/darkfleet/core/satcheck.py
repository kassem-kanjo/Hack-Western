"""Which radar pass and which area to check for one dark alert (pure, no I/O).

A dark alert says: this hull stopped broadcasting at ``t_start`` (or stopped at ``t_end``), last seen at
``last``, and its top speed bound is ``speed_cap_kn``. Its reach at time t is a circle around ``last``
with radius ``speed_cap_kn * 1.852 * hours(t - last.t)``. The satellite check reads that circle and
picks the Sentinel-1 pass closest to the dark window.

The circle is capped at ``RADIUS_CAP_KM`` so one scan reads a window small enough to finish in
minutes. A capped circle is stated as capped: the ship may have been outside it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

KM_PER_KN_HOUR = 1.852
RADIUS_CAP_KM = 30.0  # about 60 x 60 km of 10 m radar: a few minutes to read over HTTP
MIN_RADIUS_KM = 5.0  # a circle smaller than this reads a handful of pixels: not worth a scan
SEARCH_BACK = timedelta(hours=48)  # how far before the switch-off a radar pass still counts
DATATAKE_GAP = timedelta(minutes=3)  # Sentinel-1 slices of one acquisition are seconds apart

Relation = Literal["during", "before", "after"]


@dataclass(frozen=True)
class DarkWindow:
    start: datetime
    end: datetime | None  # None while the hull is still dark

    @property
    def ongoing(self) -> bool:
        return self.end is None

    def effective_end(self, now: datetime) -> datetime:
        """The window's end, or ``now`` while it is still open."""
        return self.end if self.end is not None else now


@dataclass(frozen=True)
class Area:
    center_lon: float
    center_lat: float
    radius_km: float
    reach_km: float  # the uncapped reach at the window's end
    capped: bool

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) of the circle, for a STAC search and a window read."""
        dlat = self.radius_km / 111.32
        dlon = self.radius_km / (111.32 * max(math.cos(math.radians(self.center_lat)), 1e-6))
        return (
            self.center_lon - dlon,
            self.center_lat - dlat,
            self.center_lon + dlon,
            self.center_lat + dlat,
        )


@dataclass(frozen=True)
class SceneChoice:
    index: int  # into the times the caller passed in
    relation: Relation


def parse_time(value: str) -> datetime:
    """An ISO-8601 UTC time from watch.json (the trailing Z is optional)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dark_window(alert: Mapping[str, Any]) -> DarkWindow:
    """The dark window of a watch alert: ``t_start`` to ``t_end`` (None while ongoing)."""
    end = alert.get("t_end")
    return DarkWindow(start=parse_time(alert["t_start"]), end=parse_time(end) if end else None)


def reach_area(alert: Mapping[str, Any], now: datetime) -> Area:
    """The reach circle at the end of the dark window, capped at ``RADIUS_CAP_KM``."""
    window = dark_window(alert)
    last = alert["last"]
    hours = max((window.effective_end(now) - parse_time(last["t"])).total_seconds() / 3600.0, 0.0)
    reach = float(alert["speed_cap_kn"]) * KM_PER_KN_HOUR * hours
    capped = reach > RADIUS_CAP_KM
    radius = max(min(reach, RADIUS_CAP_KM), MIN_RADIUS_KM)
    return Area(
        center_lon=float(last["lon"]),
        center_lat=float(last["lat"]),
        radius_km=radius,
        reach_km=reach,
        capped=capped,
    )


def choose_scene(times: Sequence[datetime], window: DarkWindow, now: datetime) -> SceneChoice | None:
    """The pass to use: the earliest inside the dark window, else the nearer of the closest before/after.

    A tie between "before" and "after" goes to "before": the switch-off itself is the event being
    checked, so the pass just before it is the more useful picture. None when there is no pass.
    """
    if not times:
        return None
    end = window.effective_end(now)
    inside = [i for i, t in enumerate(times) if window.start <= t <= end]
    if inside:
        first = min(inside, key=lambda i: times[i])
        return SceneChoice(index=first, relation="during")
    before = [i for i, t in enumerate(times) if t < window.start]
    after = [i for i, t in enumerate(times) if t > end]
    best_before = max(before, key=lambda i: times[i]) if before else None
    best_after = min(after, key=lambda i: times[i]) if after else None
    if best_before is None and best_after is None:
        return None
    if best_before is not None and (
        best_after is None or (window.start - times[best_before]) <= (times[best_after] - end)
    ):
        return SceneChoice(index=best_before, relation="before")
    assert best_after is not None  # at least one side exists (checked above)
    return SceneChoice(index=best_after, relation="after")


def minutes_after_switch_off(scene_time: datetime, window: DarkWindow) -> int:
    """Whole minutes from the switch-off to the pass; negative when the pass came first."""
    return round((scene_time - window.start).total_seconds() / 60.0)


def ships_it_could_have_met(alert: Mapping[str, Any]) -> int:
    """How many contacts answered yes to 'could have met' (the watch contract's ``could_meet``)."""
    return sum(1 for contact in alert.get("contacts", []) if contact.get("could_meet") is True)


def rank_dark_alerts(
    alerts: Sequence[Mapping[str, Any]], *, area_id: str | None = None, limit: int
) -> list[Mapping[str, Any]]:
    """The strongest dark alerts: most ships it could have met, then highest severity."""
    dark = [a for a in alerts if a.get("kind") == "dark" and (area_id is None or a.get("area_id") == area_id)]
    dark.sort(key=lambda a: (ships_it_could_have_met(a), float(a.get("severity") or 0.0)), reverse=True)
    return dark[: max(limit, 0)]
