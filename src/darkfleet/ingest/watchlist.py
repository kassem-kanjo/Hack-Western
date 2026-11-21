"""IMO watchlist: join the Baltic vessels we saw to the OpenSanctions maritime list.

An IMO number is a permanent hull identifier, so an exact IMO hit survives the renaming,
re-flagging and MMSI swaps a dark-fleet operator relies on; that makes it the strongest
identity signal available without buying data. Names are only a fallback because many
ships share one ("SARAH", "LEO"), so the name path keeps the conservative strength that
``ingest.sanctions`` already assigns instead of inventing a new scale here.

Reads one CSV and nothing else: no network, no writes, no state between calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from darkfleet.core.tracks import VesselInfo
from darkfleet.ingest.sanctions import build_index, load_vessels, match_vessel

# Categories worth putting in front of a judge. An entry tagged only "reg.warn" is a
# paperwork flag, not a finding, so it never reaches the watchlist.
WATCHLIST_CATEGORIES = frozenset({"sanctioned", "shadow_fleet", "detained"})

# The fields of the sanctions row the case shape exposes; the rest (flag, countries, id)
# only shaped the match strength and stay out of the snapshot.
ENTRY_FIELDS = ("caption", "imo", "risk", "url", "datasets")


def _imo_key(value: Any) -> str:
    """Digits only, so ``"IMO9427366"``, ``" 9427366 "`` and ``9427366`` key the same entry."""
    return "".join(char for char in str(value or "") if char.isdigit())


def _imo_index(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index entries by IMO, first row winning.

    Hulls are re-registered over time, so one IMO can appear on several list rows; keeping
    the first mirrors ``sanctions.build_index`` and the file's own ordering, which puts the
    oldest and usually best-documented entity first. Rows without an IMO are skipped.
    """
    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = _imo_key(entry.get("imo"))
        if key:
            index.setdefault(key, entry)
    return index


def _category(risk: list[str]) -> str:
    """The strongest risk signal, most specific first.

    Mirrors ``ingest.sanctions._category`` so IMO hits score categories exactly like name
    hits; that helper is private to the other module, so it is restated rather than imported.
    """
    if any(tag.startswith("sanction") for tag in risk):
        return "sanctioned"
    if any(tag.startswith("mare.shadow") for tag in risk):
        return "shadow_fleet"
    if any(tag.startswith("mare.detained") for tag in risk):
        return "detained"
    return "other"


def _case(vessel: VesselInfo, entry: dict[str, Any], strength: str) -> dict[str, Any]:
    """One watchlist value: the vessel as we saw it, the list entry, and why they are linked."""
    return {
        "mmsi": vessel.mmsi,
        "imo": vessel.imo,
        "name": vessel.name,
        "category": _category(entry.get("risk") or []),
        "strength": strength,
        "entry": {field: entry.get(field) for field in ENTRY_FIELDS},
    }


def imo_watchlist(vessels: list[VesselInfo], sanctions_csv: Path) -> dict[int, dict[str, Any]]:
    """Match observed vessels to the sanctions list, keyed by MMSI (issue #76).

    An exact IMO hit is reported as ``strength: "imo"``: it is the whole point of the join,
    because the name on the transponder and the flag can both be fake while the hull number
    cannot be reused on another ship. When the IMO is missing or absent from the list we
    retry on the name alone, with ``flag=None`` (no flag evidence) so a lucky name cannot
    look stronger than it is, and keep whatever strength ``ingest.sanctions`` gives
    (``strong_name``/``weak_name``). Weak hits are still returned: the UI can label them,
    and dropping them here would hide a lead an analyst may want.

    Only sanctioned, shadow-fleet and detained entries are kept, so the dict answers one
    question: which of these ships is on a list we can cite? Later vessels with the same
    MMSI overwrite earlier ones, so the last static message wins.
    """
    entries = load_vessels(sanctions_csv)
    by_imo = _imo_index(entries)
    by_name = build_index(entries)

    watchlist: dict[int, dict[str, Any]] = {}
    for vessel in vessels:
        imo_key = _imo_key(vessel.imo)
        entry = by_imo.get(imo_key) if imo_key else None
        strength = "imo"
        if entry is None:
            # mmsi=None keeps this a name-only lookup: the list has no MMSI here and an
            # MMSI hit, if one existed, would be a different (also valid) match method.
            hit = match_vessel(by_name, None, vessel.name, flag=None)
            if hit is None:
                continue
            entry = hit["entry"]
            strength = str(hit["strength"])

        case = _case(vessel, entry, strength)
        if case["category"] not in WATCHLIST_CATEGORIES:
            continue
        watchlist[vessel.mmsi] = case
    return watchlist
