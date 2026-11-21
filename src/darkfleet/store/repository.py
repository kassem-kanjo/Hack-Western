"""Storage abstractions shared by every backend.

The repository protocol is the only thing the rest of ``darkfleet`` should depend on.
Both backends implement identical semantics: writes are upserts keyed by the natural
identity of a row, so re-ingesting the same batch never duplicates anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from darkfleet.ingest.gfw import EncounterEvent, GapEvent, SarCellRow

__all__ = [
    "DarkHoursDay",
    "PositionRow",
    "Repository",
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
    """Aggregate of dark time for one UTC day."""

    day: date
    dark_hours: float
    gap_count: int


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

    def upsert_positions(self, positions: Sequence[PositionRow]) -> int:
        """Upsert positions by (mmsi, ts). Returns the number of rows submitted."""
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

    def dark_hours_daily(self, region: str) -> list[DarkHoursDay]:
        """One row per UTC day on which a gap *started*, ordered by day.

        ``dark_hours`` sums ``duration_hours`` for gaps starting that day and
        ``gap_count`` counts them. A gap is attributed to its start day only, even
        when it ends on a later day. Both backends follow this rule.
        """
        ...

    def close(self) -> None:
        """Release the underlying connection."""
        ...
