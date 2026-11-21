"""Read-only FastAPI app serving the frozen v1 API from a JSON snapshot.

The hackathon demo must never depend on a database or network at show time, so every
response is derived from files under ``DARKFLEET_DATA``. Loading is lazy and cached so
startup stays fast and each file is parsed once.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from darkfleet.api.live import router as live_router

# Only these CaseDetail fields belong in a list response; the rest are detail-only.
CASE_SUMMARY_FIELDS = (
    "id",
    "vessel",
    "off",
    "on",
    "hours",
    "score",
    "band",
    "status",
    "n_candidates",
    "n_meetings",
    "region_label",
)

STATS_FALLBACK: dict[str, Any] = {"dark_hours_daily": [], "validation": None}

app = FastAPI(title="Dark Fleet API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(live_router)


def data_dir() -> Path:
    """Resolve the snapshot directory per request so tests can point at a temp dir."""
    return Path(os.environ.get("DARKFLEET_DATA", "data/snapshot"))


@lru_cache(maxsize=8)
def _load_cases(directory: str) -> dict[str, Any]:
    """Parse cases.json once per directory; it is several MB in the real snapshot."""
    path = Path(directory) / "cases.json"
    if not path.is_file():
        raise FileNotFoundError(f"snapshot missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"cases.json must be an object, got {type(payload).__name__}")
    return payload


@lru_cache(maxsize=8)
def _load_stats(directory: str) -> dict[str, Any]:
    """Use the empty-but-valid shape until issue #27 fills validation in."""
    path = Path(directory) / "stats.json"
    if not path.is_file():
        return dict(STATS_FALLBACK)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"stats.json must be an object, got {type(payload).__name__}")
    return payload


def reset_cache() -> None:
    """Drop loaded snapshots; tests change DARKFLEET_DATA between cases."""
    _load_cases.cache_clear()
    _load_stats.cache_clear()


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    """Project a CaseDetail down to the documented CaseSummary keys."""
    return {field: case.get(field) for field in CASE_SUMMARY_FIELDS}


def _score_order(case: dict[str, Any]) -> tuple[bool, float]:
    """Sort highest score first, with unranked (null) cases last."""
    score = case.get("score")
    return (score is None, -(score or 0))


@app.get("/api/cases")
def list_cases(
    limit: int = 50,
    status: Literal["candidate", "ruled_out", "all"] = "all",
) -> dict[str, Any]:
    """Return ranked summaries; the UI never needs every frame geometry up front."""
    payload = _load_cases(str(data_dir()))
    cases = payload.get("cases", [])

    if status != "all":
        cases = [case for case in cases if case.get("status") == status]
    cases = sorted(cases, key=_score_order)
    if limit > 0:
        cases = cases[:limit]
    else:
        cases = []

    return {
        "generated_at": payload.get("generated_at"),
        "totals": payload.get("totals", {}),
        "cases": [_case_summary(case) for case in cases],
    }


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    """Serve the full detail, including geometry, behind an explicit user action."""
    payload = _load_cases(str(data_dir()))
    for case in payload.get("cases", []):
        if case.get("id") == case_id:
            return case
    raise HTTPException(status_code=404, detail="case not found")


@app.get("/api/stats")
def get_stats() -> dict[str, Any]:
    """Return chart data, falling back to an empty chart rather than a broken page."""
    return _load_stats(str(data_dir()))


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Let the demo operator confirm which snapshot is actually being served."""
    payload = _load_cases(str(data_dir()))
    return {
        "ok": True,
        "cases": len(payload.get("cases", [])),
        "source": str(data_dir()),
    }
