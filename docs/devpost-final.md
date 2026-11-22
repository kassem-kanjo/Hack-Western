# Devpost submission: paste field by field

## Project name

Dark Fleet

## Tagline

Where a ship could have gone when it switched off

## Inspiration

Every large ship carries a transponder: the radio that broadcasts its position. Some ships switch it off, often tankers moving sanctioned oil, meeting other ships at sea where nobody is watching.

A sanctions analyst or a coast guard officer asks the same question each morning: did a ship I care about switch off its transponder, and who could it have met while it was dark? We built the tool that answers it.

## What it does

Dark Fleet follows one ship at a time.

1. **A ship switches off its transponder.** This is a real case from our live recorder in the Baltic: [SHIP].
2. **A circle shows everywhere it could have gone.** It grows with a clock, at the ship's top speed, for as long as it stays silent.
3. **Ships it could have met light up.** One is on a sanctions list (matched by its permanent ID number, the IMO number, not its name): [LISTED SHIP]. We say "could have met", never "met". Nothing here proves a meeting.
4. **A satellite pass over the area.** [after #268: real Sentinel-1 pass or labelled scenario]
5. **The patrol unit.** A Raspberry Pi running QNX, with a camera, acts as a lookout. If it sees a ship and no ship is broadcasting nearby, it raises an alarm within a 200 ms deadline. If its AI dies, the screen says LOOKOUT BLIND instead of going quiet, and a standby takes over in 130 to 157 ms.
6. **The scale.** In the Strait of Hormuz, 2 in 3 ships seen by satellite radar were not broadcasting from March to June 2026.

The app has three screens.

- **Hormuz.** Two years of satellite radar. 32% of radar-seen ships were not broadcasting in 2025; 68% from March to June 2026, after the strikes of 28 February. The Gulf of Oman next door rose too, so we compared the two: Hormuz rose 16 points more (95% CI 3 to 27). At the 8 April ceasefire Hormuz fell from 66% to 46% while its neighbour rose.
- **Watch.** Live. We have recorded transponder broadcasts from six regions since Friday night. Click a ship that went quiet and you see how long it has been dark, how far it could have gone, and every ship it could have met.
- **Patrol unit.** The QNX lookout described above.

## How we built it

- A recorder takes one live feed across six regions and writes hourly files.
- A Python engine does the physics: how far a ship could have gone at its top speed, whether two ships could have met, and whether a jump in position is possible at all. A silence only counts if our own receivers were hearing that area.
- Sanctions matching uses the IMO number. Names are easy to change; the number is not.
- Radar comes from Global Fishing Watch. We also run our own ship detection on Sentinel-1 radar scenes from Microsoft Planetary Computer: 35 ships in the 21 November pass.
- Tiger Data (TimescaleDB) is the live store. A sink streams every recorded position into a `positions` hypertable (over 590,000 rows by Sunday 00:20 ET). A per-minute continuous aggregate gives the live position reports a minute.
- FastAPI behind a TypeScript front end with MapLibre and deck.gl.
- On the Pi: Python on QNX 8, a small C helper for the camera, and YOLOX-nano run through OpenCV's DNN module from QNX's own package repository.

AI coding agents wrote most of the code in separate lanes. We reviewed every change and ran it on real recorded data before merging.

## Challenges we ran into

- **Our Hormuz receivers hear only about 17 ships.** Too few for live stories there, so Hormuz is told with radar and the live stories come from the Baltic.
- **Global Fishing Watch records meetings only between fishing boats.** We cannot say who a tanker met in Hormuz, only who it could have met.
- **Three of the four ships we first called "listed" were port-inspection records, not sanctions.** We now count only ships on sanctions or shadow-fleet lists.
- **The Pi's safety loop stalled on its own disk writes**, up to 270 ms. Moving the writes off the loop fixed it.
- **The deadline depended on priority order.** Under the same overload, 10 of 27 alarms met 200 ms before we fixed the priorities and 63 of 64 after. Giving the AI a higher priority than the camera froze the camera.

## Accomplishments that we're proud of

- **Our own ship detection on Sentinel-1 radar.** We did not only consume a feed: we ran detection on raw scenes and found 35 ships in the 21 November pass.
- **We caught a bug in our own validation.** Our "next door" comparison area, the Gulf of Oman, sat inside the Hormuz box it was compared against, so we were comparing a region with part of itself. We re-ran it with a strait-only area. Hormuz went from 31.9% to 67.5%, and the gap with the neighbour grew from 14.5 to 15.8 points, as the bias predicted. We published the corrected figure.
- **A measured real-time guarantee.** Under a 4-core overload, 10 of 27 alarms met the deadline without our priority guard and 63 of 64 with it. The safety loop's worst delay fell from 24 ms to 1.8 ms.
- **Physics that clears ships.** On 12 March, 10 ships appeared to switch off far out in the Arabian Sea and reappeared 22 hours later at one spot off the UAE. That trip needs about 29 knots; none of them can sail that fast. Their positions were faked, probably by GPS interference. We do not flag them.

## What we learned

A "dark share" on its own mixes jamming, fewer ships and small boats. A neighbouring control area is what turned a striking chart into a finding we can defend.

Using a real-time operating system is not enough. Which work sits where in the priority order, and what blocks inside the loop, decided whether the deadline held.

A store that loaded once looks alive for hours. Only checking the values, not the status codes, showed our live rates read zero.

## What's next

More regions and more receivers, a real ship photo test on the Pi, and a lookout on a patrol boat. Commercial transponder and radar feeds for real clients: ours are non-commercial.

## Built with

python, fastapi, typescript, vite, maplibre, deck.gl, timescaledb, tiger-data, postgresql, qnx, raspberry-pi, opencv, yolox, global-fishing-watch, sentinel-1, microsoft-planetary-computer, opensanctions, aisstream, pytest, hypothesis

## Try it out

- GitHub: https://github.com/kassem-kanjo/Hack-Western
- Video: [link]

## Prize tracks

**Best Use of QNX.** The lookout runs on QNX 8 on a Raspberry Pi 5 with a webcam, and uses the AI module from QNX's open-source repository (OpenCV DNN with YOLOX-nano). The use case is real-time reliability: a lookout that must alarm within 200 ms and must never go silently blind. A safety loop at the top fixed priority owns the decision, and the AI runs lower and is untrusted. If anything stalls for 800 ms the screen says LOOKOUT BLIND, and a standby AI takes over in 130 to 157 ms. Under overload, 63 of 64 alarms met the deadline against 10 of 27 before the priority fix. Sightings go to a hash-chained log on the power-safe qnx6 file system. We demo it on the Pi at the table.

**[MLH] Best Use of Tiger Data.** Tiger Data is our live store, not a dump. Every position our recorder hears streams into a `positions` hypertable (over 590,000 rows by Sunday 00:20 ET). A per-minute continuous aggregate drives the live position reports a minute shown on screen. The same database holds 475 days of Hormuz radar, 25,836 radar cells and 566 encounter events. We found that a store loaded once looks alive while no new data arrives, so we check values, not status codes.

**Most Adventurous.** We ran our own ship detection on satellite radar, put a safety-critical lookout on an embedded real-time system, and tested our own headline finding until it broke and we fixed it. The result lets anyone see where ships could have gone when they stopped telling anyone.

## Attribution

Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.
