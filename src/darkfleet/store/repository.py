"""Storage abstractions shared by every backend.

The repository protocol is the only thing the rest of ``darkfleet`` should depend on.
Both backends implement identical semantics: writes are upserts keyed by the natural
identity of a row, so re-ingesting the same batch never duplicates anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, TypedDict, overload

from darkfleet.core.tracks import CoverageMinute, Ping, VesselInfo
from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow

__all__ = [
    "DarkHoursBucket",
    "DarkHoursDay",
    "PositionRow",
    "Repository",
    "SarDailyRow",
]


@dataclass(frozen=True, slots=True)
class PositionRow:
    """One AIS position report. ``ts`` is a timezone-aware UTC datetime."""

    ts: datetime
    mmsi: str
    lat: float
    lon: float
    sog: float | None


@dataclass(frozen=True, slots=True)
class DarkHoursDay:
    """Region-scoped daily aggregate (the original shape, kept for existing callers)."""

    day: date
    dark_hours: float
    gap_count: int


class DarkHoursBucket(TypedDict):
    """One UTC day of the fleet-wide dark-hours aggregate."""

    day: date
    hours: float
    gaps: int


@dataclass(frozen=True, slots=True)
class SarDailyRow:
    """One region/day of the unseen-fleet radar series.

    ``seen`` is every radar detection, ``broadcasting`` those also seen on AIS and
    ``dark`` the remainder, so ``seen == broadcasting + dark`` is an invariant of the
    writer. Aggregates only: no vessel identity ever reaches this row.
    """

    day: date
    region: str
    seen: int
    broadcasting: int
    dark: int


class Repository(Protocol):
    """Persistence boundary for ingested GFW data."""

    def upsert_gaps(self, region: str, gaps: Sequence[GapEvent]) -> int:
        """Upsert gaps by GFW event id. Returns the number of rows submitted."""
        ...

    def upsert_encounters(self, region: str, encounters: Sequence[EncounterEvent]) -> int:
        """Upsert encounters by GFW event id. Returns the number of rows submitted."""
        ...

    def upsert_sar_cells(self, region: str, cells: Sequence[SarCellRow]) -> int:
        """Upsert SAR cells by (region, day, lat, lon). Returns the number of rows submitted."""
        ...

    def upsert_sar_daily(self, rows: Sequence[SarDailyRow]) -> int:
        """Upsert the unseen-fleet daily series by (region, day). Returns rows submitted.

        Writes are idempotent so rebuilding the same snapshot can be replayed into the
        store without duplicating days. Backends that materialise aggregates refresh
        them after the batch.
        """
        ...

    def upsert_positions(self, positions: Sequence[PositionRow]) -> int:
        """Upsert positions by (mmsi, ts). Returns the number of rows submitted."""
        ...

    def add_pings(self, pings: Sequence[Ping]) -> int:
        """Upsert raw AIS pings by (mmsi, ts). Returns the number of rows submitted.

        The caller owns batching: one call is one transaction, so a loader sends chunks of a
        few thousand pings rather than the whole recording.
        """
        ...

    def pings_between(self, start: datetime, end: datetime) -> list[Ping]:
        """AIS pings with ``start <= t <= end`` (both inclusive), ordered by ``(t, mmsi)``."""
        ...

    def upsert_vessels(self, vessels: Sequence[VesselInfo]) -> int:
        """Upsert static data by MMSI, keeping the row's supplied ``last_seen``.

        Returns the number of rows submitted. ``last_seen`` is trusted from the caller: a
        loader reading an old recording must not stamp old vessels as seen today.
        """
        ...

    def vessels(self) -> list[VesselInfo]:
        """Every known vessel, ordered by MMSI."""
        ...

    def set_coverage(self, minutes: Sequence[CoverageMinute]) -> int:
        """Upsert recorder coverage by minute. Returns the number of rows submitted."""
        ...

    def coverage_between(self, start: datetime, end: datetime) -> list[CoverageMinute]:
        """Coverage rows with ``start <= minute <= end``, ordered by minute."""
        ...

    def gaps_in(self, region: str, start: date, end: date) -> list[GapEvent]:
        """Gaps whose start UTC date falls in ``[start, end]``, ordered by start."""
        ...

    def encounters_in(self, region: str, start: date, end: date) -> list[EncounterEvent]:
        """Encounters whose start UTC date falls in ``[start, end]``, ordered by start."""
        ...

    def sar_cells_in(self, region: str, start: date, end: date) -> list[SarCellRow]:
        """SAR cells whose UTC day falls in ``[start, end]``, ordered by day, lat, lon."""
        ...

    @overload
    def dark_hours_daily(self, region: str) -> list[DarkHoursDay]:
        """One row per UTC day on which a gap *started*, for one region, ordered by day.

        ``dark_hours`` sums ``duration_hours`` for gaps starting that day and
        ``gap_count`` counts them. A gap is attributed to its start day only, even
        when it ends on a later day. Both backends follow this rule.
        """
        ...

    @overload
    def dark_hours_daily(
        self,
        start: date,
        end: date,
        *,
        refresh: bool = False,
    ) -> list[DarkHoursBucket]:
        """Fleet-wide daily dark hours for gap start days in ``[start, end]``.

        Returns one ``{"day", "hours", "gaps"}`` bucket per UTC day that had at least
        one gap, ordered by day. ``hours`` sums ``duration_hours`` of gaps starting
        that day and ``gaps`` counts them (a gap belongs to its start day only).
        ``refresh=True`` asks the backend to bring the daily aggregate up to date for
        the window before reading it; backends that compute on read ignore it.
        """
        ...

    def close(self) -> None:
        """Release the underlying connection."""
        ...
