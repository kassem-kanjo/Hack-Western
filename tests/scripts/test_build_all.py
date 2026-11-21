"""Prove scripts/build_all.py runs every builder, writes the manifest, and gates the exit code.

The builders themselves are monkeypatched: each fake writes a tiny file into the temp data dir, so
the test exercises build_all's ordering, manifest and required-file checks without touching real
recordings or GFW.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_all.py"
REGION = "BALTIC_WATCH"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_all", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_all = _load_script()


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def _patch_builders(monkeypatch, data: Path, *, write_unseen: bool = True) -> None:
    out = data / "snapshot"

    def fake_snapshot() -> None:
        _write(out / "cases.json", {"generated_at": "2026-11-21T06:00:00Z", "totals": {"gaps": 4}})
        _write(out / "stats.json", {"validation": {}})

    def fake_live(argv: list[str]) -> int:
        region = Path(_flag(argv, "--region")).name
        _write(
            Path(_flag(argv, "--out")) / f"live_{region}.json",
            {"region": region, "generated_at": "2026-11-21T06:00:00Z", "counts": {"pings": 12}},
        )
        return 0

    def fake_unseen(argv: list[str]) -> int:
        if not write_unseen:
            return 2
        _write(
            Path(_flag(argv, "--out")),
            {"period": {"start": "2026-08-15", "end": "2026-11-15"}, "counts": {"days": 3}},
        )
        return 0

    def fake_history(argv: list[str]) -> int:
        _write(Path(_flag(argv, "--out")), {"period": {"end": "2026-11-15"}, "counts": {"total": 1}})
        return 0

    monkeypatch.setattr(build_all.build_snapshot, "main", fake_snapshot)
    monkeypatch.setattr(build_all.build_live, "main", fake_live)
    monkeypatch.setattr(build_all.build_unseen_fleet, "main", fake_unseen)
    monkeypatch.setattr(build_all.build_hormuz_history, "main", fake_history)


def _prepare(tmp_path: Path, monkeypatch, *, write_unseen: bool = True) -> Path:
    data = tmp_path / "data"
    (data / "cases").mkdir(parents=True)
    (data / "cases" / "cases_v2.json").write_text("{}", encoding="utf-8")
    (data / "cache" / "ais" / REGION).mkdir(parents=True)
    _patch_builders(monkeypatch, data, write_unseen=write_unseen)
    return data


def test_all_present_writes_manifest_and_exits_zero(tmp_path: Path, monkeypatch) -> None:
    data = _prepare(tmp_path, monkeypatch)

    code = build_all.main(["--data-dir", str(data), "--regions", REGION])

    assert code == 0
    manifest = json.loads((data / "snapshot" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["built_at"].endswith("Z")
    files = {entry["file"] for entry in manifest["datasets"]}
    assert files == {
        "cases.json",
        "stats.json",
        f"live_{REGION}.json",
        "hormuz_unseen.json",
        "hormuz_history.json",
    }

    cases = next(entry for entry in manifest["datasets"] if entry["file"] == "cases.json")
    assert cases["bytes"] == (data / "snapshot" / "cases.json").stat().st_size
    assert cases["generated_at"] == "2026-11-21T06:00:00Z"
    assert cases["counts"] == {"gaps": 4}


def test_missing_required_dataset_exits_one(tmp_path: Path, monkeypatch, capsys) -> None:
    data = _prepare(tmp_path, monkeypatch, write_unseen=False)

    code = build_all.main(["--data-dir", str(data), "--regions", REGION])

    assert code == 1
    assert (data / "snapshot" / "manifest.json").is_file()
    assert "hormuz_unseen.json" in capsys.readouterr().err


def test_existing_gfw_file_is_kept_when_the_builder_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    data = _prepare(tmp_path, monkeypatch, write_unseen=False)
    existing = data / "snapshot" / "hormuz_unseen.json"
    _write(existing, {"period": {"end": "2026-11-15"}, "counts": {"days": 1}})
    before = existing.read_bytes()

    code = build_all.main(["--data-dir", str(data), "--regions", REGION])

    assert code == 0
    assert existing.read_bytes() == before
    assert "kept existing" in capsys.readouterr().err


def test_missing_region_folder_is_skipped(tmp_path: Path, monkeypatch, capsys) -> None:
    data = _prepare(tmp_path, monkeypatch)

    code = build_all.main(["--data-dir", str(data), "--regions", "NOWHERE"])

    assert code == 1
    assert "no recording" in capsys.readouterr().err
