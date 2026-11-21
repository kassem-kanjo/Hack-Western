# Dark Fleet: feasibility (2026-11-20, late Friday)

**Verdict: feasible at $0. One account-approval risk; see "Risks".**

## Data (all free)
| Source | What it gives | Cost / limits | Notes |
|---|---|---|---|
| Global Fishing Watch API v3 | AIS "intentional off" gap events (`public-global-gaps-events:latest`), at-sea encounters (`public-global-encounters-events:latest`), Sentinel-1 radar vessel detections with a "no AIS match" filter (`public-global-sar-presence:latest` via 4Wings), vessel identity, risk indicators. Docs include a ready-made "Detect dark vessels" workflow. | Free, **non-commercial only** (CC BY-NC 4.0). 50,000 requests/day, 1.5M/month. Python package available. | Must show "Powered by Global Fishing Watch" with a link. Token must stay on our backend, never in the browser. Gap events need 12 h of silence; data updated daily, so this is an investigation view (days), not live. |
| aisstream.io | Live AIS positions over WebSocket, filtered by bounding box | Free key, beta, no SLA | Gives the console a live layer. Key also stays server-side. |
| xView3 (DIUx-xView) | Labelled radar dataset + reference models for dark-vessel detection | Free | Prior art and possible model weights; not needed for the MVP. |

## Build (no paid services)
- Backend: Python (FastAPI) proxy + ingest; GFW and aisstream keys server-side.
- Store: Tiger Data (Postgres + time-series), free tier or local container.
- Console: MapLibre GL + deck.gl (no map API key needed).
- Edge unit: Python + ONNX Runtime + a permissively licensed "boat" detector (COCO class), two-process design (untrusted AI process + high-priority safety core + watchdog). Same code moves to a QNX Pi (`python3-onnxruntime` is on oss.qnx.com).
- Hosting/domain: Vultr free credits, GoDaddy domain via the MLH claim code.

## Risks
1. **GFW account eligibility (small risk).** Per the Authentication page, tokens are **issued instantly** after registering (no manual approval step documented); a 403 on a specific dataset means requesting access to that dataset. Still, Their terms say registrants apply on behalf of an organisation that supports sustainable use of ocean resources, from that organisation's email. A student hackathon project may need approval or be refused. Action: Kassem applies now with his university email and describes it as a non-commercial student research prototype. Fallback if refused or slow: aisstream.io live AIS + our own gap detection (a ship silent > N hours inside our watch box), which still gives a real dark-vessel signal without satellite radar.
2. **Not live.** GFW events lag by about a day. Frame the console as investigation + live AIS layer.
3. **Commercial use.** Real clients would need a commercial AIS/radar feed (GFW is non-commercial). Say this plainly in the pitch.
4. **Edge model licence.** Avoid AGPL detectors (e.g. some YOLO releases) in favour of Apache/MIT models.

## Sources
- https://globalfishingwatch.org/our-apis/documentation
- https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits
- https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats
- https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/key-concepts
- https://aisstream.io/documentation
- https://arxiv.org/pdf/2206.00897v4.pdf (xView3-SAR)
