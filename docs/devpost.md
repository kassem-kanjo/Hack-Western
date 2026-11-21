# Dark Fleet

**Tagline:** Finds ships that hide, using physics, not guesses.

## Inspiration

Ships that want to hide switch off their transponder. Radar satellites still see them. Most tools stop at "this ship went dark" and never answer what an analyst asks next: where could it have been, and who could it have met? I wanted a physics answer a compliance team could defend.

## What it does

Around the Strait of Hormuz, on a typical day 41% of the vessels radar sees have no matching location broadcast (the median over 59 well-covered days, 15 August to 15 November). Some are small boats that don't have to broadcast; many are not.

Dark Fleet finds those gaps and tests them. For each gap it builds the area the ship could have reached between its last and next known position at its class speed. Radar detections inside it are candidates; a Brownian-bridge model says where the ship most likely was.

It also reads our live AIS recording. In one Baltic morning 4 unrelated ships each jumped 40-51 km off their route to the same point on land north of St Petersburg, and back, at speeds no ship can reach. That is spoofing, caught by kinematics.

From 5,624 AIS gaps of 6 to 96 hours, 109 had an unmatched radar detection inside the ship's possible-reach area, and 19 stay unexplained.

Every alert says why it is an alert. Powered by Global Fishing Watch.

## How we built it

A recorder holds one aisstream WebSocket across six regions and writes hourly gzip files. Ingest turns those into tracks. One Python engine handles silences, impossible jumps, spoof ghost points, meetings with anchorage context, and an IMO watchlist. A second path pulls Global Fishing Watch gaps, builds the prism and the bridge, and lines them up with Sentinel-1 scene times.

Storage is Tiger Data / TimescaleDB: positions, gaps, encounters and radar cells as hypertables, with continuous aggregates for the hourly and daily views. FastAPI serves read-only JSON from a snapshot, so the demo runs offline. The console is Vite, TypeScript, MapLibre and deck.gl on a bundled basemap.

AI coding agents (DeepSeek via OpenCode) wrote most of the code in separate lanes, one commit each. Every change was reviewed and run on real recorded data in a browser before merge.

## Challenges we ran into

**Radar near a dark ship was no better than chance.** Against time-shifted gaps: 108 real matches against 100.5 expected, ratio 1.07, interval 0.91 to 1.25. The interval includes 1, so we claim no radar signal and never accuse on radar alone.

**"Inside the prism" carries almost no information.** A position moved 50-150 km at random still lands inside a typical prism 98.4% of the time. The bridge fixes localisation (AUC 0.87) but cannot create a signal the data lacks.

**Our best case failed our own physics.** CL YICHUN looked like a perfect opener until the arithmetic said reaching the radar dots needed 18.4 kn average against 10.8 kn straight-line.

**The early spoof detector flagged a river port** in St Petersburg. I tightened the rule to an impossible jump in *and* out, and it disappeared.

**The held-out test outside Brazil was inconclusive:** 4 real against 5.7 shifted.

## Accomplishments we're proud of

A tool that rules cases out. Of 109 gaps with radar inside the possible-reach area, 90 are cleared with a stated reason: stationary, outside the lens at the pass time, out of reach at class speed, or outside the gap.

At least 21 sanctioned or shadow-fleet vessels in the Baltic in about 9 hours, matched by the IMO number they broadcast. IMO is a hard identity; names are not.

Two real offshore ship-to-ship transfers, both routine bunkering, and we label them that way.

## What we learned

Most alarms are innocent, and physics can prove it. A dramatic anecdote is not evidence. A name is not an identity: a name-only join matched 7 of 109 cases, and 5 were generic names that almost certainly belong to different ships. Publishing the median beats publishing the mean.

## What's next

A land mask and a spatial shuffle, so the null test runs in space as well as time. Vessel-level attribution of radar detections, which would make the prism check a real recall number. Commercial AIS and radar feeds. Then the edge unit: a camera and a detector beside a small trusted core, so a patrol boat can flag a ship it sees that is not broadcasting.

## Built with

python, fastapi, uvicorn, shapely, pyproj, numpy, httpx, websockets, psycopg, timescaledb, tigerdata, postgresql, typescript, vite, maplibre, deck.gl, pytest, hypothesis, ruff, aisstream, global-fishing-watch, copernicus-sentinel-1, opensanctions, natural-earth, onnxruntime, opencv, deepseek, opencode

## Prize tracks

**Most Adventurous (Arc'teryx).** The prize asks for something that enables others to discover the unknown. That is the product: it takes a ship that vanished and gives back the places it could have been and the contacts it could have met. The demo opens on a real region, and the judge can look up any number on screen.

**MLH Best use of Tiger Data.** Tiger Data (TimescaleDB) is our store, not a bolt-on. Our own AIS recording lives in a `positions` hypertable: 265,250 pings from four regions at the last load. A continuous aggregate (`ais_hourly`) turns them into the per-hour chart on the Global screen, refreshed for the window before each export. Radar cells (25,836 rows) and encounter events (566) sit in their own hypertables, with daily and weekly aggregates for the radar view. One honest gap: Global Fishing Watch logs almost no gap events inside the strait, so our `dark_hours_daily` aggregate is empty there, and the console says nothing rather than draw a zero chart.

**Vultr.** TODO (pending Kassem)

**GoDaddy.** TODO (pending Kassem)

**Entire.** TODO (pending Kassem)

**QNX.** TODO (pending Kassem)
