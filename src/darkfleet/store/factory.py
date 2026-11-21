"""Choose a repository backend from configuration."""

from __future__ import annotations

import os
from pathlib import Path

from darkfleet.store.repository import Repository
from darkfleet.store.sqlite_repo import SqliteRepo
from darkfleet.store.timescale_repo import TimescaleRepo

__all__ = ["make_repo"]

DEFAULT_SQLITE_PATH = "data/cache/darkfleet.sqlite"


def make_repo(
    sqlite_path: str | Path = DEFAULT_SQLITE_PATH,
    database_url: str | None = None,
) -> Repository:
    """Return a Timescale repo when a database URL is configured, else SQLite.

    ``database_url`` wins over ``DATABASE_URL``. The URL is never logged.
    """
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL")
    if url:
        return TimescaleRepo(url)
    return SqliteRepo(sqlite_path)
