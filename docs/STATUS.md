# Status

Updated: Sun 22 Nov 2026, 13:40 ET

## Where it stands
- Built at Hack Western 13 (20-22 Nov 2026). Submitted on Devpost; judged on Sunday.
- Demo video: https://youtu.be/AwbrCT3uajE
- Write-up: https://dev.to/kassemkanjo/dark-fleet-we-spent-hack-western-chasing-ships-that-switch-off-their-transponders-2p9

## What runs
- **Recorder** (`scripts/record_ais.py`): live AIS from six regions into hourly files, with `scripts/recorder_watchdog.sh`. Restart: `nohup caffeinate -is ~/.local/bin/uv run python scripts/record_ais.py --regions BALTIC_WATCH,HORMUZ,MALAYSIA_EOPL,LACONIA,RED_SEA,BLACK_SEA > logs/record_all.log 2>&1 &`
- **Watch builder** (`scripts/build_watch.py --loop 60 --hours 12`): rebuilds `data/snapshot/watch.json` every minute.
- **Tiger sink** (`scripts/tiger_sink.py --loop 30`): positions into a Tiger Data hypertable.
- **API + console** (`uvicorn darkfleet.api.app:app`, port 8000): serves `web/dist` and the snapshot.
- **Patrol unit**: QNX 8 on a Raspberry Pi 5, deployed with `scripts/edge_deploy.sh` (see `docs/patrol-demo.md`).

## Next
- README refresh to match the final product (Hormuz numbers, the "Follow one ship" story, the Patrol unit).
- Open issues on GitHub are the backlog.
