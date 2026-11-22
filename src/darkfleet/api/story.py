"""Story-case API (issue #274): the frozen demo case and its satellite scenario.

``scripts/freeze_case.py`` writes the files this router serves. Like the rest of ``api/`` it is
read-only and file-backed, and it resolves ``DARKFLEET_DATA`` on every request so tests and the
desktop app can point it at another snapshot without a restart.

A case that has not been frozen yet is a 404, not an empty object: the story screen needs to
know the difference between "no case" and "a case with no contacts".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from darkfleet.api.watch import data_dir

router = APIRouter(tags=["story"])

CASE_FILE = "story_case.json"
SATCHECK_FILE = "story_case/satcheck.json"
CHIP_DIR = "story_case/chip"
STORY_NOT_FROZEN = "story case not frozen yet"
SATCHECK_NOT_FROZEN = "story satcheck not frozen yet"
CHIP_NOT_FOUND = "satcheck chip not found"

_CHIP_NAME = re.compile(r"^[0-9]{3}$")


def _json_response(path: Path, missing_detail: str) -> JSONResponse:
    """Serve one frozen JSON file, or a JSON 404 when it has not been written."""
    if not path.is_file():
        raise HTTPException(status_code=404, detail=missing_detail)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="frozen story file is unreadable") from exc
    return JSONResponse(content=payload, headers={"Cache-Control": "no-cache"})


@router.get("/api/story")
def get_story() -> JSONResponse:
    """The frozen case: the picked alert, its hull, contacts, background and satcheck."""
    return _json_response(data_dir() / CASE_FILE, STORY_NOT_FROZEN)


@router.get("/api/story/satcheck")
def get_story_satcheck() -> JSONResponse:
    """The scenario satellite payload, with chip URLs pointing back at this router."""
    return _json_response(data_dir() / SATCHECK_FILE, SATCHECK_NOT_FROZEN)


@router.get("/api/story/satcheck/chip/{name}.png")
def get_story_chip(name: str) -> FileResponse:
    """One scenario chip. The name is a three-digit stem; anything else is a 404."""
    if not _CHIP_NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail=CHIP_NOT_FOUND)
    path = data_dir() / CHIP_DIR / f"{name}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=CHIP_NOT_FOUND)
    return FileResponse(path, media_type="image/png")
