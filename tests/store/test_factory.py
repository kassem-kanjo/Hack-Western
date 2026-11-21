from __future__ import annotations

from pathlib import Path

import pytest

from darkfleet.store import factory
from darkfleet.store.sqlite_repo import SqliteRepo


def test_make_repo_defaults_to_sqlite_without_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    repo = factory.make_repo(tmp_path / "x.sqlite")

    assert isinstance(repo, SqliteRepo)
    repo.close()


def test_make_repo_empty_database_url_uses_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-be-used")

    repo = factory.make_repo(tmp_path / "x.sqlite", database_url="")

    assert isinstance(repo, SqliteRepo)
    repo.close()


def test_make_repo_routes_url_to_timescale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    recorded: list[str] = []
    sentinel = object()

    def fake_timescale_repo(url: str) -> object:
        recorded.append(url)
        return sentinel

    monkeypatch.setattr(factory, "TimescaleRepo", fake_timescale_repo)

    repo = factory.make_repo(tmp_path / "x.sqlite", database_url="postgresql://invalid")

    assert repo is sentinel
    assert recorded == ["postgresql://invalid"]


def test_make_repo_reads_database_url_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid")
    recorded: list[str] = []
    sentinel = object()

    def fake_timescale_repo(url: str) -> object:
        recorded.append(url)
        return sentinel

    monkeypatch.setattr(factory, "TimescaleRepo", fake_timescale_repo)

    assert factory.make_repo(tmp_path / "x.sqlite") is sentinel
    assert recorded == ["postgresql://invalid"]
