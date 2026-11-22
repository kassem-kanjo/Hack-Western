"""Hash-chained sightings log: every "vessel not on AIS" alert, recorded on the Pi.

One JSON line per sighting::

    {"seq": 7, "t_utc": "...", "frame_sha256": "...", "patrol": {...}, "verdict": "...",
     "alarm": {...}, "prev": "<hash of seq 6>", "hash": "<sha256 of this record without 'hash'>"}

Each record carries the hash of the one before it (the first carries 64 zeros), so editing,
deleting or reordering any record is detected unless the whole log is rewritten (a rewrite
recomputes every hash, and only an outside copy of the last hash would catch that).
``frame_sha256`` is the digest of the raw camera frame the AI looked at, so the picture can be
matched to the record later.

Appends happen on a side thread with fsync, never in the safety core's 20 Hz loop. The deployed
Pi keeps the file on ``/data``, a QNX6 filesystem (copy-on-write with a power-safe design); even
so a power cut can leave a half-written last line. ``verify`` reports that as a torn tail (the
chain up to it is intact) rather than as tampering.

    python -m darkfleet.edge.sightings verify ~/darkfleet/sightings.jsonl
    python -m darkfleet.edge.sightings tail ~/darkfleet/sightings.jsonl
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sys
import threading
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64
FILE_NAME = "sightings.jsonl"


def record_hash(record: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of the record without its own ``hash`` field."""
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_records(path: str) -> tuple[list[dict[str, Any]], str | None]:
    """Complete records, plus the text of a torn (unparseable) final line if there is one."""
    records: list[dict[str, Any]] = []
    torn: str | None = None
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return [], None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            if all(not rest.strip() for rest in lines[i + 1 :]):
                torn = line  # a half-written last line after a power cut
                break
            records.append({"_unparseable": line})  # damage in the middle is reported by verify
            continue
        records.append(obj)
    return records, torn


def verify(path: str) -> dict[str, Any]:
    """Walk the chain. ``ok`` is False on any broken link; a torn tail alone does not break it."""
    records, torn = read_records(path)
    prev = GENESIS
    for index, rec in enumerate(records):
        problem = None
        if "_unparseable" in rec:
            problem = "unparseable line"
        elif rec.get("seq") != index + 1:
            problem = f"sequence {rec.get('seq')} where {index + 1} was expected (line removed or reordered)"
        elif rec.get("prev") != prev:
            problem = "previous-hash link broken"
        elif rec.get("hash") != record_hash(rec):
            problem = "record content does not match its hash (edited)"
        if problem:
            return {
                "ok": False,
                "records": index,
                "broken_at": index + 1,
                "problem": problem,
                "torn_tail": torn is not None,
            }
        prev = rec["hash"]
    last = records[-1] if records else None
    return {
        "ok": True,
        "records": len(records),
        "last_seq": None if last is None else last["seq"],
        "last_t_utc": None if last is None else last.get("t_utc"),
        "last_hash": prev,
        "torn_tail": torn is not None,
    }


class SightingsLog:
    """Appends chained records from a side thread; ``submit`` is cheap and never touches disk."""

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._seq, self._prev = self._resume()
        self._queue: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="sightings", daemon=True)
        self._thread.start()

    def _resume(self) -> tuple[int, str]:
        """Continue an existing chain; a torn last line is cut off so the chain stays clean."""
        records, torn = read_records(self.path)
        if torn is not None:
            self._truncate_torn()
        for rec in reversed(records):
            if "hash" in rec and "seq" in rec:
                return int(rec["seq"]), str(rec["hash"])
        return 0, GENESIS

    def _truncate_torn(self) -> None:
        try:
            with open(self.path, "rb") as fh:
                data = fh.read()
            cut = data.rstrip(b"\n").rfind(b"\n") + 1
            with open(self.path, "wb") as fh:
                fh.write(data[:cut])
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass

    def submit(self, sighting: dict[str, Any]) -> None:
        self._queue.put(sighting)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                self._append(item)
            except OSError:
                pass  # a full disk must not stop the unit

    def _append(self, sighting: dict[str, Any]) -> None:
        record = {
            "seq": self._seq + 1,
            "t_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            **sighting,
            "prev": self._prev,
        }
        record["hash"] = record_hash(record)
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self._seq, self._prev = record["seq"], record["hash"]

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=3.0)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("verify", "tail"):
        print("usage: python -m darkfleet.edge.sightings verify|tail <sightings.jsonl>", file=sys.stderr)
        return 2
    command, path = args
    if command == "tail":
        records, _ = read_records(path)
        for rec in records[-5:]:
            print(json.dumps(rec, sort_keys=True))
        return 0
    result = verify(path)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
