"""OpenSanctions maritime list: load vessel entries and match one vessel against them.

The list is published under CC BY-NC 4.0, so the real export stays out of the repository
(``data/sanctions/`` is gitignored) and only this parser plus a small sample are committed.
Matching is deliberately conservative: an exact MMSI hit is strong, and so is an
exact-but-normalised name hit that carries a hull number or a flag shared with the case
vessel. Any other name hit is common enough that it is probably a different ship, so it is
reported as weak and still carries a note that an analyst must confirm it.

This module only reads one CSV; it does no network I/O.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

SANCTIONS_SOURCE = "OpenSanctions maritime (CC BY-NC 4.0)"
NAME_MATCH_NOTE = "name match only, needs analyst confirmation"

VESSEL_TYPE = "VESSEL"

# Entry fields exposed to the API; the raw row also carries countries/flag/id, which drive
# the name-match strength but are not part of the case shape.
ENTRY_FIELDS = ("caption", "imo", "mmsi", "risk", "url", "datasets")

# GFW reports a vessel flag as ISO 3166-1 alpha-3 while OpenSanctions uses alpha-2. Most
# codes share their first two letters, so only the exceptions need an entry here; the rest
# fall back to the first two letters. The table is complete for ISO 3166-1: without it a
# prefix such as "CHN" would wrongly match Switzerland ("CH") and "MLT" would match Mali.
ISO3_TO_ISO2 = {
    "AGO": "AO",
    "ALA": "AX",
    "AND": "AD",
    "ARE": "AE",
    "ATA": "AQ",
    "ATF": "TF",
    "ATG": "AG",
    "AUT": "AT",
    "BEN": "BJ",
    "BES": "BQ",
    "BHS": "BS",
    "BIH": "BA",
    "BLR": "BY",
    "BLZ": "BZ",
    "BRB": "BB",
    "CHN": "CN",
    "COD": "CD",
    "COG": "CG",
    "COK": "CK",
    "COM": "KM",
    "CPV": "CV",
    "CUW": "CW",
    "CYM": "KY",
    "ESH": "EH",
    "EST": "EE",
    "FLK": "FK",
    "GNB": "GW",
    "GNQ": "GQ",
    "GRD": "GD",
    "IRL": "IE",
    "ISR": "IL",
    "KAZ": "KZ",
    "KOR": "KR",
    "LBR": "LR",
    "MAC": "MO",
    "MDG": "MG",
    "MLT": "MT",
    "MNE": "ME",
    "MOZ": "MZ",
    "MYT": "YT",
    "PAK": "PK",
    "PRK": "KP",
    "PYF": "PF",
    "SLB": "SB",
    "SLV": "SV",
    "SRB": "RS",
    "SVN": "SI",
    "SYC": "SC",
    "TUR": "TR",
    "TUV": "TV",
    "UKR": "UA",
}


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

    Each vessel is normalised once: ``mmsi`` and ``imo`` are digits, ``risk``,
    ``countries`` and ``datasets`` are lists, ``aliases`` is a list of non-empty names, and
    text fields are stripped. The returned order is the file order.
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
                    "flag": str(row.get("flag") or "").strip(),
                    "countries": _split(row.get("countries")),
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


def _iso2(value: Any) -> str | None:
    """Normalise one flag to ISO alpha-2; None when it is not a usable code.

    Two-letter codes pass through, three-letter codes use the table or fall back to their
    first two letters. Anything else (a country name, an empty cell) is not a code.
    """
    code = str(value or "").strip().upper()
    if len(code) == 2 and code.isalpha():
        return code
    if len(code) == 3 and code.isalpha():
        return ISO3_TO_ISO2.get(code, code[:2])
    return None


def _shares_flag(vessel: dict[str, Any], flag: Any) -> bool:
    """True when the case vessel's flag appears on the entry's flag or countries.

    The case flag may be ISO2 or ISO3; an absent or malformed flag is never a match, so
    an unsure comparison keeps the name match weak.
    """
    case_code = _iso2(flag)
    if case_code is None:
        return False
    countries = vessel.get("countries") or []
    if isinstance(countries, str):
        countries = _split(countries)
    entry_codes = {_iso2(code) for code in [vessel.get("flag"), *countries]}
    return case_code in entry_codes


def _name_strength(vessel: dict[str, Any], name_key: str, flag: Any) -> str:
    """A name hit is strong only with a hull number or a shared flag; else it is weak."""
    if any(char.isdigit() for char in name_key) or _shares_flag(vessel, flag):
        return "strong_name"
    return "weak_name"


def _entry(vessel: dict[str, Any]) -> dict[str, Any]:
    return {field: vessel.get(field) for field in ENTRY_FIELDS}


def match_vessel(
    index: dict[str, dict[str, Any]],
    mmsi: Any,
    name: Any,
    flag: Any = None,
) -> dict[str, Any] | None:
    """Match one vessel against the index; ``None`` when neither key hits.

    An exact MMSI match wins and is reported as ``method: "mmsi"``. Otherwise an exact
    normalised name match (caption or alias) is reported as ``method: "name"`` with the
    extra ``note`` telling the analyst it needs confirmation. A name match is
    ``strength: "strong_name"`` only when the name carries a hull number or the entry's
    flag matches ``flag``; any other name hit is ``strength: "weak_name"`` because a
    common name usually belongs to a different ship. The category comes from the entry's
    risk tags: sanctioned, shadow_fleet, detained, or other.
    """
    mmsi_key = _digits(mmsi)
    if mmsi_key:
        vessel = index.get(mmsi_key)
        if vessel is not None and (vessel.get("mmsi") or "") == mmsi_key:
            return _result(vessel, "mmsi", "mmsi")

    name_key = normalise_name(name)
    if name_key:
        vessel = index.get(name_key)
        if vessel is not None and name_key in _name_keys(vessel):
            return _result(vessel, "name", _name_strength(vessel, name_key, flag))
    return None


def _result(vessel: dict[str, Any], method: str, strength: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "match": True,
        "method": method,
        "strength": strength,
        "entry": _entry(vessel),
        "category": _category(vessel.get("risk") or []),
        "source": SANCTIONS_SOURCE,
    }
    if method == "name":
        result["note"] = NAME_MATCH_NOTE
    return result
