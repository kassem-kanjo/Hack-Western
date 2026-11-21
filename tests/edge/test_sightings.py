"""The hash-chained sightings log: appends, tamper detection, torn tails and resuming."""

from __future__ import annotations

import json

from darkfleet.edge.sightings import GENESIS, SightingsLog, verify


def _log(tmp_path, n):
    path = str(tmp_path / "sightings.jsonl")
    log = SightingsLog(path)
    for i in range(n):
        log.submit(
            {"kind": "patrol_sighting", "frame_sha256": f"{i:064x}", "patrol": {"lat": 1.0, "lon": 2.0}}
        )
    log.close()
    return path


def _lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def test_chain_is_linked_and_verifies(tmp_path):
    path = _log(tmp_path, 4)
    records = [json.loads(line) for line in _lines(path)]
    assert [r["seq"] for r in records] == [1, 2, 3, 4]
    assert records[0]["prev"] == GENESIS
    assert all(records[i]["prev"] == records[i - 1]["hash"] for i in range(1, 4))
    result = verify(path)
    assert result["ok"] and result["records"] == 4 and result["last_seq"] == 4 and not result["torn_tail"]


def test_editing_deleting_or_reordering_breaks_the_chain(tmp_path):
    path = _log(tmp_path, 4)
    lines = _lines(path)
    edited = json.loads(lines[1])
    edited["patrol"]["lat"] = 9.9
    cases = {
        "edit": [lines[0], json.dumps(edited, sort_keys=True, separators=(",", ":")), *lines[2:]],
        "delete": [lines[0], *lines[2:]],
        "reorder": [lines[0], lines[2], lines[1], lines[3]],
    }
    for name, content in cases.items():
        bad = tmp_path / f"{name}.jsonl"
        bad.write_text("\n".join(content) + "\n", encoding="utf-8")
        result = verify(str(bad))
        assert not result["ok"], name
        assert result["broken_at"] in (2, 3), (name, result)


def test_torn_last_line_is_reported_but_the_chain_up_to_it_is_intact(tmp_path):
    path = _log(tmp_path, 3)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"seq":4,"t_utc":"2026-')  # power cut mid-write
    result = verify(path)
    assert result["ok"] and result["records"] == 3 and result["torn_tail"]


def test_reopening_continues_the_chain_and_drops_a_torn_tail(tmp_path):
    path = _log(tmp_path, 2)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"seq":3,"half')
    log = SightingsLog(path)
    log.submit({"kind": "patrol_sighting", "frame_sha256": "f" * 64})
    log.close()
    result = verify(path)
    assert result["ok"] and result["records"] == 3 and not result["torn_tail"]
