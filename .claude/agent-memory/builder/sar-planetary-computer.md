---
name: sar-planetary-computer
description: Facts learned building the Sentinel-1 ship detector (#90): PC access, speed, tuning pitfalls
metadata:
  type: project
---

- Planetary Computer works anonymously: STAC `sentinel-1-rtc` (orthorectified UTM, linear power, same-day) beats `sentinel-1-grd` (GCP-only, not georeferenced). SAS token via /api/sas/v1/token/{collection}. rasterio window read over HTTP is slow: ~0.2 Mpx/s (100 Mpx Hormuz scene = 4-6 min) even with 6 threads.
- Newest S1 pass (02:06 UTC) is two 25 s slices; bbox only partly covered. Sentinel-2 often has no same-day scene (2-5 day revisit); fall back up to 6 days.
- Our HORMUZ AIS recorder sits on the Gulf side (~55.1E) and only started 08:00 UTC 2026-11-21: no overlap with radar scan area or scene time, so matching cannot be validated on real data.
- Detector pitfalls: low contrast gate (6x) gives ~800 false alarms (coast, wind streaks); 25x (14 dB) + radar-derived bright-land mask + 800 m coast reject gives 35. Natural Earth coast alone misses radar-bright shores.
- Sandbox hook rejects some long bash heredocs ("too complex"); write helper scripts with the Write tool and run them separately. macOS sed -i needs a suffix arg; use python for edits.
