"""Read-only FastAPI app serving the frozen v1 API from a JSON snapshot.

The hackathon demo must never depend on a database or network at show time, so every
response is derived from files under ``DARKFLEET_DATA``. Loading is lazy and cached so
startup stays fast and each file is parsed once.
"""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from darkfleet.api.areas import router as areas_router
from darkfleet.api.edge import router as edge_router
from darkfleet.api.hormuz import router as hormuz_router
from darkfleet.api.live import router as live_router
from darkfleet.api.sar import router as sar_router
from darkfleet.api.satcheck import router as satcheck_router
from darkfleet.api.search import router as search_router
from darkfleet.api.story import router as story_router
from darkfleet.api.vessels import router as vessels_router
from darkfleet.api.vessels import warm_cache as warm_vessel_cache
from darkfleet.api.watch import router as watch_router

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

# The Global Fishing Watch query window the snapshot was built from (docs/feasibility-dark-fleet.md F3,
# docs/api-schema.md). A configuration constant of the build, not a measured result.
GAP_WINDOW = {"start": "2026-08-15", "end": "2026-11-15"}

STATS_FALLBACK: dict[str, Any] = {"dark_hours_daily": [], "validation": None}

# On the public server (DARKFLEET_PUBLIC=1) the interactive API docs are switched off; the
# console only needs the JSON routes, and the docs page lists every endpoint to strangers.
_PUBLIC = os.environ.get("DARKFLEET_PUBLIC", "").strip() == "1"
app = FastAPI(
    title="Dark Fleet API",
    version="1.0.0",
    docs_url=None if _PUBLIC else "/docs",
    redoc_url=None if _PUBLIC else "/redoc",
    openapi_url=None if _PUBLIC else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(live_router)
app.include_router(hormuz_router)
app.include_router(edge_router)
app.include_router(watch_router)
app.include_router(vessels_router)


@app.on_event("startup")
def _warm_vessel_cache() -> None:
    """Parse the recent recording in the background so the first dossier opens at once."""
    threading.Thread(target=warm_vessel_cache, name="warm-vessels", daemon=True).start()


app.include_router(search_router)
app.include_router(areas_router)
app.include_router(sar_router)
app.include_router(satcheck_router)
app.include_router(story_router)


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


def _own_track_block(directory: str) -> dict[str, Any]:
    """Own-track and silence-detector results, read from the generated validation files.

    ``data/validation/tracks_results.json`` gives the hidden-route test; the silence
    detector's "where receivers were up" count comes from the table in
    ``docs/recall-misses.md`` (found / in-scope, minus the misses explained by our own
    recorder outages). Missing files simply omit the keys: the UI hides those rows.
    """
    import re

    root = Path(os.path.abspath(directory)).parent.parent
    block: dict[str, Any] = {}

    tracks = root / "data" / "validation" / "tracks_results.json"
    if tracks.is_file():
        try:
            containment = json.loads(tracks.read_text(encoding="utf-8")).get("containment") or {}
            if "inside_lens_at_truth_time" in containment and "n" in containment:
                block["hidden_route_inside"] = containment["inside_lens_at_truth_time"]
                block["hidden_route_total"] = containment["n"]
        except (ValueError, OSError):
            pass

    recall = root / "docs" / "recall-misses.md"
    if recall.is_file():
        text = recall.read_text(encoding="utf-8")
        counts = re.search(r"In-scope gaps: (\d+); found: (\d+); missed: (\d+)", text)
        coverage = re.search(r"\| Receiver coverage below floor \| (\d+) \|", text)
        if counts and coverage:
            in_scope, found, missed = (int(counts.group(i)) for i in (1, 2, 3))
            outage_misses = int(coverage.group(1))
            up_total = in_scope - outage_misses
            # Only a clean "found all where receivers were up" claim if every miss is an outage.
            if missed == outage_misses and found <= up_total:
                block["silence_found_receivers_up"] = found
                block["silence_total_receivers_up"] = up_total
    return block


@lru_cache(maxsize=8)
def _load_stats_full(directory: str) -> dict[str, Any]:
    stats = dict(_load_stats(directory))
    stats["gap_window"] = GAP_WINDOW
    own_track = _own_track_block(directory)
    if own_track:
        stats["own_track"] = own_track
    return stats


def reset_cache() -> None:
    """Drop loaded snapshots; tests change DARKFLEET_DATA between cases."""
    _load_cases.cache_clear()
    _load_stats.cache_clear()
    _load_stats_full.cache_clear()


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
    return _load_stats_full(str(data_dir()))


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Let the demo operator confirm which snapshot is actually being served."""
    payload = _load_cases(str(data_dir()))
    return {
        "ok": True,
        "cases": len(payload.get("cases", [])),
        "source": str(data_dir()),
    }


def web_dist() -> Path | None:
    """Resolve the optional built web app; unset or missing means API-only."""
    raw = os.environ.get("DARKFLEET_WEB_DIST", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


@lru_cache(maxsize=4)
def _static_app(directory: str) -> StaticFiles:
    """One StaticFiles instance per directory; switching directories is rare."""
    return StaticFiles(directory=directory, html=True)


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_web(full_path: str, request: Request) -> Response:
    """Serve the built SPA on every non-API path, with index.html as the fallback.

    Registered after the API routes so ``/api`` always wins. Unknown ``/api`` paths stay
    404 JSON instead of turning into the SPA; the browser needs to see real API errors.
    """
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    directory = web_dist()
    if directory is None:
        raise HTTPException(status_code=404, detail="Not Found")
    static = _static_app(str(directory))
    try:
        return await static.get_response(full_path, request.scope)
    except StarletteHTTPException as exc:
        if exc.status_code != 404:
            raise
    # Client-side routes such as /live/BALTIC_WATCH are not files; hand back the app.
    return await static.get_response("index.html", request.scope)
