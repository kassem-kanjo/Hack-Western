"""Issue #237: the Pi re-walks the sightings chain only when the log changed, and the deploy
script refuses to start the status server without a token.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from darkfleet.edge import sightings, status_http

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "edge_deploy.sh"


@pytest.fixture(autouse=True)
def _clear_verify_cache() -> None:
    status_http._verify_cache.clear()


def _counting_verify(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = sightings.verify

    def counted(path: str) -> dict[str, Any]:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(sightings, "verify", counted)
    return calls


def test_verdict_is_reused_while_the_log_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "sightings.jsonl"
    log.write_text("", encoding="utf-8")
    calls = _counting_verify(monkeypatch)

    first = status_http._verified(str(log))
    second = status_http._verified(str(log))

    assert first == second
    assert len(calls) == 1


def test_an_append_forces_a_fresh_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "sightings.jsonl"
    log.write_text("", encoding="utf-8")
    calls = _counting_verify(monkeypatch)

    status_http._verified(str(log))
    with log.open("a", encoding="utf-8") as handle:
        handle.write("x\n")
    # Force a different mtime even on filesystems with coarse timestamps.
    stat = log.stat()
    os.utime(log, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    status_http._verified(str(log))

    assert len(calls) == 2


def test_missing_log_is_verified_without_caching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting_verify(monkeypatch)
    missing = str(tmp_path / "absent.jsonl")

    status_http._verified(missing)
    status_http._verified(missing)

    assert len(calls) == 2
    assert missing not in status_http._verify_cache


def test_deploy_refuses_to_start_the_status_server_without_a_token() -> None:
    env = {key: value for key, value in os.environ.items() if key != "EDGE_STATUS_TOKEN"}
    result = subprocess.run(
        ["bash", str(SCRIPT), "qnxuser@192.0.2.1"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "EDGE_STATUS_TOKEN is not set" in result.stderr
