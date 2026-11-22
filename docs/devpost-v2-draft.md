# Dark Fleet

Dark Fleet finds ships that switched off their transponders, shows where they could have gone, and who they could have met.

## Inspiration

Ships are supposed to broadcast their position. Some switch it off. Usually that's tankers moving sanctioned oil, meeting other ships at sea where nobody is watching.

We wanted to answer one question a sanctions analyst or a coast guard asks every morning: did a ship they care about switch off its transponder, and who could it have met while it was dark?

## What it does

There are three screens.

**Hormuz.** We pulled two years of satellite radar for the Strait of Hormuz. Before the war, 32% of the ships radar saw there weren't broadcasting. Since the strikes on 28 Feb 2026 it's 66%. To check that isn't just bad reception, we compared it with the Gulf of Oman next door. Hormuz went up 14.5 points more (95% CI 2 to 26).

Two real stories sit under that. SEA 1, a ship on a shadow-fleet list, switched off for 13.1 hours in the Gulf of Oman, nine days after the strikes, and came back 96 km further west, closer to the strait. And on 12 March, 10 ships "switched off" far out in the Arabian Sea and came back 22 hours later at one spot off the UAE. That trip needs 29 knots. None of them can go that fast, so their positions were faked, probably by GPS interference. The app doesn't flag them as hiding.

We also ran our own ship detection on the Sentinel-1 radar pass from 21 Nov: 35 ships.

**Watch.** This one is live. We've been recording AIS from six regions since Friday night. When a ship on a sanctions or shadow-fleet list stops broadcasting, or any ship goes dark near one, it shows up here. Click it and you see how long it's been dark, how far it could have gone, and every ship it could have met in that time. During the hackathon it caught ships like GIVING, a cargo ship that went dark in the Baltic with 19 ships in reach, including sanctioned tankers.

**Patrol unit.** A Raspberry Pi 5 running QNX with a webcam. It's a lookout: if the camera sees a ship and no ship is broadcasting nearby, it raises an alert within 200 ms. The AI is treated as untrusted. A separate safety loop owns the decision. If the camera or the AI stops, it says "lookout blind" instead of going quiet. Kill the AI and a standby takes over in about 0.15 s. Every sighting is written to a hash-chained log on QNX's power-safe file system.

## How we built it

- An AIS recorder on one websocket across six regions, writing hourly files.
- A Python engine that does the physics: how far a ship could have gone at its top speed, whether two ships could have met, and whether a jump in position is even possible. A silence only counts if our receivers were actually hearing that area.
- Sanctions matching on the ship's permanent IMO number, not its name. Names are easy to change.
- Satellite radar from Global Fishing Watch, plus our own ship detection on Sentinel-1 scenes from Microsoft Planetary Computer.
- Tiger Data (TimescaleDB) as the live store. A sink streams every recorded ping into a `positions` hypertable (527,773 rows at the time of writing), and a per-minute continuous aggregate gives the live rates on screen.
- FastAPI and a TypeScript front end with MapLibre and deck.gl. It also runs as a Mac app.
- On the Pi: Python on QNX 8, a small C helper for the camera, and YOLOX-nano through OpenCV's DNN module from QNX's own package repository.

## Challenges

- **Our Hormuz receivers only hear about 17 ships.** Too few for live stories there, so Hormuz is told with radar and the live stories come from the Baltic.
- **Global Fishing Watch only records meetings between fishing boats.** So we can't say who a tanker met in Hormuz, only who it could have met.
- **Three of the four "listed" ships we first found were port-inspection records, not sanctions.** We now only count ships on sanctions or shadow-fleet lists.
- **The Pi's safety loop was stalling on its own disk writes**, up to 270 ms. Moving the writes off the loop fixed it.
- **The real-time guarantee depended on priority order.** Under the same overload, 10 of 27 alerts met the deadline before we fixed the priorities and 63 of 64 after. Putting the AI above the camera froze the camera.

## What we learned

The physics clears ships, not just accuses them. A ship that "went dark" but would have needed 29 knots to reappear where it did didn't hide; its position was wrong.

Checking against a neighbouring area is what turned a scary chart into a finding we can defend.

## What's next

More regions, more receivers, and putting the lookout on a real patrol boat.

## Attribution

Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.

## Built with

python, fastapi, typescript, vite, maplibre, deck.gl, timescaledb, tiger-data, postgresql, qnx, raspberry-pi, opencv, yolox, global-fishing-watch, sentinel-1, microsoft-planetary-computer, opensanctions, aisstream, pytest, hypothesis

## Prize tracks

**QNX.** The Pi runs QNX 8 with a webcam and the AI module from oss.qnx.com (OpenCV DNN, YOLOX-nano). The safety loop runs at the highest fixed priority and owns the alert; the AI runs lower and is untrusted. Under a 4-core overload, 10 of 27 alerts met the 200 ms deadline without the priority fix and 63 of 64 with it, and the loop's worst delay dropped from 24 ms to 1.8 ms. If anything stalls for 500 ms it says "lookout blind". A standby AI takes over in 130–157 ms. Sightings go to a hash-chained log on the power-safe qnx6 partition. Adaptive partitioning isn't in this QNX image, so we used fixed priorities.

**Best use of Tiger Data.** Tiger is the live store. Every ping our recorder hears is streamed into a `positions` hypertable, and the live "position reports a minute" on screen comes from a per-minute continuous aggregate. It also holds 475 days of Hormuz radar, 25,836 radar cells and 566 encounter events.

**Most Adventurous.** It lets anyone see where ships went when they stopped telling anyone.
