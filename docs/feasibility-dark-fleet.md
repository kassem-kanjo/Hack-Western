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

## Gate results
| Gate | Result | Evidence |
|---|---|---|
| F5 live AIS | **PASS** (Fri 23:08 ET) | aisstream, Baltic bbox [53.5-66N, 9.5-30.5E], 60 s: 763 messages, 591 unique vessels (362 position reports, 206 static data). Coverage is dense enough to detect silences live. |
| F1 GFW access | **PASS** (Fri 23:20 ET) | Token works; events endpoint (POST /v3/events with geometry) returns gaps, encounters, loitering. |
| F3 dark activity, Baltic | **FAIL** | 0 gap events Sep 1-Nov 15 in the Baltic box: GFW counts "intentional disabling" only > 50 nm offshore (research.md). Encounters 522 / loitering 3,289 exist but are fishing-centric. |
| F3 other regions | **PASS** | Gap events Aug 15-Nov 15: global 86,438; Gulf of Oman/Arabian Sea 55 (cargo + non-fishing); Argentine EEZ edge 130 (fishing); West Africa 1,040 (mostly gear buoys, filter out). Each gap has off/on positions, times, implied speed: exactly the prism inputs. |
| F2 SAR granularity | **PASS** (Hormuz) / FAIL (Baltic) | 4Wings `public-global-sar-presence` at HIGH res = 0.01° cells (~1 km), DAILY date (no time of day). Oct 2026, Gulf of Oman box [54-60E, 22-27N]: 11,036 detection cells, **5,551 unmatched to AIS**. Gulf of Finland: 1 detection. |

**Decision (Chief, Fri 23:25): demo region moves from the Baltic to the Strait of Hormuz / Gulf of Oman.** Rich radar coverage, real gap events, and the centre of sanctioned Iranian oil STS activity. SAR has day resolution only, so a detection matches a prism if its cell intersects the union of lenses over that day.
| F5 live AIS, Hormuz | **FAIL** | aisstream, box [22-27.5N, 50-60.5E], 90 s: 3 messages, 2 vessels. Volunteer receivers barely cover Iran/Oman. |
| F6 storage | **PASS** | Tiger Cloud service up: TimescaleDB 2.32.1 on PostgreSQL 18.7 (trial, $1,000 credit, no card). |

**Decision (Chief, Fri 23:55): live layer = Baltic / Danish-straits watchlist** (591 vessels/min coverage): track sanctioned tankers live and alert when one goes silent. Investigation (satellite + prisms) stays in Hormuz. Same shadow fleet, two theatres.

## Live coverage check, first hour of the six-region recorder (Sat 03:51-04:55 ET)
| Region | Messages | Positions | Unique vessels | Msgs/min | Verdict |
|---|---|---|---|---|---|
| Baltic | 46,861 | 26,236 | 3,465 | 732 | Strong: all detectors |
| Malaysia EOPL | 3,069 | 2,420 | 668 | 50 | Usable: watchlist (IMO), ship-to-ship meetings; silences only with local coverage |
| Black Sea | 1,276 | 959 | 156 | 21 | Thin: watchlist only |
| Hormuz | 65 | 62 | 16 | 1.5 | **Not usable live** (~4 positions per ship per hour) |
| Laconia, Red Sea | 0 | 0 | 0 | 0 | None |

**Decision (Chief):** Hormuz stays the lead story on satellite radar + GFW history (Decision 0002 fallback);
live detection runs on the Baltic and Malaysia EOPL. Recorder keeps all six regions in case coverage changes.
