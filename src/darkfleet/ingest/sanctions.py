"""OpenSanctions maritime list: load vessel entries and match one vessel against them.

The list is published under CC BY-NC 4.0, so the real export stays out of the repository
(``data/sanctions/`` is gitignored) and only this parser plus a small sample are committed.
Matching is deliberately conservative: an exact MMSI hit is the only strong signal, while a
name hit is exact-but-normalised and always carries a note that an analyst must confirm it.

This module only reads one CSV; it does no network I/O.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

SANCTIONS_SOURCE = "OpenSanctions maritime (CC BY-NC 4.0)"
NAME_MATCH_NOTE = "name match only, needs analyst confirmation"

VESSEL_TYPE = "VESSEL"

# Entry fields exposed to the API; the raw row also carries countries/flag/id, which the
# case shape does not use.
ENTRY_FIELDS = ("caption", "imo", "mmsi", "risk", "url", "datasets")


def _digits(value: Any) -> str:
    """Keep only the digits, so 'IMO9427366' and ' 9427366 ' both become '9427366'."""
    return "".join(char for char in str(value or "") if char.isdigit())


def _split(value: Any) -> list[str]:
    """Split a ';'-separated cell, dropping empty parts and surrounding whitespace."""
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def normalise_name(value: Any) -> str:
    """Upper-case ASCII letters and digits only, so spacing/punctuation do not matter."""
    return "".join(char for char in str(value or "").upper() if char.isascii() and char.isalnum())


def load_vessels(path: Path | str) -> list[dict[str, Any]]:
    """Parse the maritime CSV and keep VESSEL rows only.

    Each vessel is normalised once: ``mmsi`` and ``imo`` are digits, ``risk`` and
    ``datasets`` are lists, ``aliases`` is a list of non-empty names, and text fields are
    stripped. The returned order is the file order.
    """
    vessels: list[dict[str, Any]] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("type") or "").strip().upper() != VESSEL_TYPE:
                continue
            vessels.append(
                {
                    "caption": str(row.get("caption") or "").strip(),
                    "imo": _digits(row.get("imo")),
                    "mmsi": _digits(row.get("mmsi")),
                    "risk": _split(row.get("risk")),
                    "aliases": _split(row.get("aliases")),
                    "url": str(row.get("url") or "").strip(),
                    "datasets": _split(row.get("datasets")),
                }
            )
    return vessels


def _name_keys(vessel: dict[str, Any]) -> set[str]:
    names = [vessel.get("caption"), *vessel.get("aliases", [])]
    return {key for key in (normalise_name(name) for name in names) if key}


def build_index(vessels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index vessels by MMSI and by normalised name (caption and every alias).

    The MMSI pass runs first so a numeric name can never shadow a real MMSI. Duplicate
    names keep the first vessel in file order, which mirrors how the list is loaded.
    """
    index: dict[str, dict[str, Any]] = {}
    for vessel in vessels:
        mmsi = vessel.get("mmsi") or ""
        if mmsi:
            index.setdefault(mmsi, vessel)
    for vessel in vessels:
        for key in _name_keys(vessel):
            index.setdefault(key, vessel)
    return index


def _category(risk: list[str]) -> str:
    """The strongest risk signal on the entry, most specific first."""
    if any(tag.startswith("sanction") for tag in risk):
        return "sanctioned"
    if any(tag.startswith("mare.shadow") for tag in risk):
        return "shadow_fleet"
    if any(tag.startswith("mare.detained") for tag in risk):
        return "detained"
    return "other"


def _entry(vessel: dict[str, Any]) -> dict[str, Any]:
    return {field: vessel.get(field) for field in ENTRY_FIELDS}


def match_vessel(
    index: dict[str, dict[str, Any]],
    mmsi: Any,
    name: Any,
) -> dict[str, Any] | None:
    """Match one vessel against the index; ``None`` when neither key hits.

    An exact MMSI match wins and is reported as ``method: "mmsi"``. Otherwise an exact
    normalised name match (caption or alias) is reported as ``method: "name"`` with the
    extra ``note`` telling the analyst it needs confirmation. The category comes from the
    entry's risk tags: sanctioned, shadow_fleet, detained, or other.
    """
    mmsi_key = _digits(mmsi)
    if mmsi_key:
        vessel = index.get(mmsi_key)
        if vessel is not None and (vessel.get("mmsi") or "") == mmsi_key:
            return _result(vessel, "mmsi")

    name_key = normalise_name(name)
    if name_key:
        vessel = index.get(name_key)
        if vessel is not None and name_key in _name_keys(vessel):
            return _result(vessel, "name")
    return None


def _result(vessel: dict[str, Any], method: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "match": True,
        "method": method,
        "entry": _entry(vessel),
        "category": _category(vessel.get("risk") or []),
        "source": SANCTIONS_SOURCE,
    }
    if method == "name":
        result["note"] = NAME_MATCH_NOTE
    return result
