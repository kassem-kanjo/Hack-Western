# Validating the Baltic engine on our own AIS tracks

Generated: 2026-11-21T08:56:17+00:00
Seed: 7

## Result

No data. The darkfleet store has no AIS positions yet, so there is nothing to hide and nothing to recover. This is expected on a fresh checkout and is not a test failure.

Source checked: positions in the darkfleet store.

## What to run once the recorder has data

    ~/.local/bin/uv run python scripts/validate_tracks.py

or, before the ingest lane exists, point it at raw aisstream files:

    ~/.local/bin/uv run python scripts/validate_tracks.py --jsonl-dir data/cache/ais

Data: our own AIS recorder. No Global Fishing Watch data is used in this report.
