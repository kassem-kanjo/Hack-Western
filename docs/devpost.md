# Dark Fleet

**One line:** Dark Fleet shows which ships switched off their transponders where it matters, and who they could have met while dark.

## Inspiration

I started from the question a sanctions analyst asks in the morning: did any ship I care about go dark, or meet someone, and where? The data exists. Ships broadcast, radar satellites look, lists keep names. But most tools stop at "this ship went dark", and cannot tell hiding from a dead receiver. I wanted an answer a compliance desk or a coast guard could defend, so I built it from physics and public lists, not a score.

## What it does

**Watch.** The app opens on the strongest live story, not a map. A ship in the Baltic went dark for {dark_duration} while a sanctioned tanker was within reach. Three figures: how long it was dark, how far it could have got, how many ships it could have met. The reach shape is on the map, and a could-have-met table names the ships by flag and list status. Click a row to open the vessel.

**Hormuz.** One chart, two story cards. Since the war began on 28 Feb, 66% of the vessels satellite radar sees in the strait have no matching broadcast, up from 32%. The Gulf of Oman next door went from 26% to 46%, so the difference is +14.5 points, 95% CI 2 to 26; the chart marks 28 Feb. Below it, SEA 1, on a shadow-fleet list, went dark for 13.1 hours in the Gulf of Oman nine days after the strikes and came back 96 km west, closer to the strait. The counter-example: on 12 March, 10 ships stopped broadcasting within two hours, last seen up to 1,334 km out in the Arabian Sea, and came back about 22 hours later at one spot off the UAE. That trip needs 29 knots, faster than any of them can sail, so their positions were corrupted (likely GPS interference). We do not rank them as hiding.

**Patrol unit.** A QNX 8 Raspberry Pi 5 with a camera, for when no signal arrives. An AI lookout flags a vessel in view that is not on AIS, inside a 200 ms deadline. Overload the Pi and alerts still land on time: under the same load, without our priority guard 10 of 27 alerts met the deadline, with it 63 of 64; kill it and the unit says LOOKOUT BLIND while a warm standby takes over in about 0.15 s, 130 to 157 ms on the Pi. The detector sits outside the safety core, so a slow model cannot stall the watch.

## How we built it

One AIS recorder holds a single websocket across six regions and writes hourly gzip files; ingest turns those into tracks. A Python engine applies the physics: a silence only counts where our receivers were up, a jump has to be impossible in and out, and a meeting has to clear the anchorage. Watchlist matches use the IMO number a ship broadcasts, because a name is not an identity.

The radar side pulls Global Fishing Watch vessel detections and gaps, and we run our own ship detection on Sentinel-1 scenes through Microsoft Planetary Computer. Tiger Data is the store, not a cache: positions, gaps, encounters and radar cells are hypertables, and a per-minute continuous aggregate (`ais_minutely`) feeds the live area numbers. FastAPI serves read-only JSON from a snapshot so the demo runs offline; the console is MapLibre and deck.gl on a bundled basemap. The edge unit is Python on QNX 8 with a small C helper for the camera (QNX's libcamapi), OpenCV DNN running YOLOX-nano from QNX's own package repository, and a safety loop that runs at a higher fixed priority than the detector.

We tested the Hormuz result against a control area next door with a day-block bootstrap, because a rise in one place means little if the water beside it rose too.

## Challenges we ran into

**Our Hormuz receivers hear about 17 ships.** Too few to carry a live layer, so the live story runs on our Baltic receivers and Hormuz leads with radar. Better to show the region where the data is strong than a thin feed dressed up as coverage.

**Global Fishing Watch meetings only cover fishing boats.** Its encounters come from fishing behaviour, so a tanker meeting a tanker is not in it. We use it for what it covers and do not call it a full meeting record.

**Ten ships that "went dark" but never could have.** A naive detector would call ten simultaneous transponder gaps ten ships hiding. Their own positions say otherwise: to reappear where they did, they would have needed 29 knots for 22 hours. Physics turns ten alarms into one finding: the positions were corrupted.

**The safety loop stalled on its own disk writes.** The evidence log sat on the same path as the watch, and a slow write delayed a tick. We moved the writes off the loop, and it holds its deadline under load.

## What we learned

Physics clears more cases than it accuses, and saying why is the product. A gap with no radar inside the reach is a ship that ran in the open; a jump that needs a speed no hull can make is a bad position, not a bad ship. I also learned to write nothing when the data is missing: an empty aggregate is honest, a zero chart is a lie.

## What's next

More edge units, so a patrol boat can carry the lookout without a server. Commercial AIS and radar feeds where public coverage is thin. Vessel-level attribution of radar detections, which would turn the reach test into a recall number. More regions on the same engine, since nothing in the physics is specific to Hormuz.

## Attribution

Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Satellite radar from Copernicus Sentinel-1, accessed via Microsoft Planetary Computer.

## Built with

python, fastapi, uvicorn, shapely, pyproj, numpy, httpx, websockets, psycopg, timescaledb, tigerdata, postgresql, typescript, vite, maplibre, deck.gl, pytest, hypothesis, ruff, aisstream, global-fishing-watch, copernicus-sentinel-1, opensanctions, natural-earth, onnxruntime, opencv, qnx, raspberry-pi, yolox, deepseek, opencode

## Prize tracks

**Most Adventurous (Arc'teryx).** The prize asks for something that lets others discover the unknown. That is the product: it takes a ship that vanished and gives back the places it could have been and the contacts it could have met. The demo opens on a real region, and the judge can check any number on screen against the panel that produced it.

**MLH Best use of Tiger Data.** Tiger Data (TimescaleDB) is our store, not a bolt-on. Our AIS recording lives in a `positions` hypertable: {positions_rows} pings from six regions. A per-minute continuous aggregate (`ais_minutely`) feeds the live area numbers, and an hourly aggregate draws the per-hour chart. Radar cells ({radar_cells} rows) and encounter events ({encounters}) sit in their own hypertables. One honest gap: Global Fishing Watch logs almost no gap events inside the strait, so `dark_hours_daily` is empty there, and the console says nothing rather than draw a zero chart.

**Vultr.** TODO (pending Kassem)

**GoDaddy.** TODO (pending Kassem)

**Entire.** TODO (pending Kassem)

**QNX.** The Patrol unit runs on QNX 8 on a Raspberry Pi 5 with a USB webcam, and its AI (YOLOX-nano via OpenCV DNN from oss.qnx.com) is treated as untrusted. A safety core at SCHED_FIFO 30 owns the verdict and a 200 ms deadline; under a 4-core overload, alerts on time went from 10/27 without our priority guard to 63/64 with it, and the safety loop's worst lateness from 24 ms to 1.8 ms. If the camera or AI stalls for 500 ms the core raises LOOKOUT BLIND instead of going quiet; a warm standby detector takes over in 130-157 ms. Every sighting is hash-chained to QNX's power-safe qnx6 partition, so the evidence survives a pulled plug. What we could not use: adaptive partitioning and HAM are not in this image, so the guard is fixed priorities.
