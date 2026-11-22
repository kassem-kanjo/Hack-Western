# Devpost submission (as saved on Devpost, Sun 22 Nov ~01:50 ET)

## Project name

Dark Fleet

## Tagline

Where a ship could have gone when it switched off

## Inspiration

Big ships carry a transponder, a radio that broadcasts where they are. Some switch it off, often tankers moving sanctioned oil to another ship at sea, where nobody can see the transfer.

Sanctions teams and coast guards ask the same question every morning: did a ship we care about go quiet overnight, and who could it have met? We wanted to build the tool they'd open to answer it.

## What it does

When a ship switches off its transponder, Dark Fleet shows everywhere it could have gone since, which other ships it could have met, and whether any of those are on a sanctions list. Then it helps you look.

**Watch (live).** We've been recording ships in six regions since Friday night. When one goes quiet, the app draws a circle from its last position and top speed. It then checks every other ship's own track. In one case on Saturday night, 186 ships were inside the circle and only 45 could have been in the same place at the same time. Ships on a sanctions list are matched by IMO number, which stays the same when a ship is renamed, and the app says which list.

Two ships side by side at sea with their transponders off is how sanctioned oil changes hands. We can't prove a meeting happened, but we can show where to look.

**Satellite check.** For a ship that went dark, we pull the newest Sentinel-1 radar image over its circle and run our own ship detector on it. Radar sees ships whether they broadcast or not, so anything it sees that isn't broadcasting shows up red. So far no pass has caught a live ship while it was dark (the closest came 17 hours early), and the app says that plainly.

**Patrol unit.** A Raspberry Pi running QNX with a webcam, on our table. If it sees a boat and nothing nearby is broadcasting, it raises an alarm within 200 ms. If its AI stops, it says "lookout blind" instead of going quiet.

**Hormuz.** Two years of satellite radar over the Strait of Hormuz. About a third of the ships radar saw weren't broadcasting in 2025; from March to June 2026, after the strikes, it was about two thirds. The Gulf of Oman next door rose too, so the jump isn't just bad reception: Hormuz rose about 16 points more (95% CI 3 to 27).

## How we built it

- A Python recorder holds one connection to a live transponder feed across six regions and writes every position to disk and to Tiger Data.
- A Python engine does the physics: how far a ship could have gone, whether two ships could have met, and whether a jump in position is even possible. A silence only counts if our receivers were hearing that area at the time.
- Satellite radar history comes from Global Fishing Watch. For live checks we run our own detection on Sentinel-1 scenes from Microsoft Planetary Computer.
- FastAPI and a TypeScript front end with MapLibre.
- On the Pi: QNX 8, a small C helper for the camera, and YOLOX-nano through OpenCV's DNN module from QNX's own package repository. A safety loop at the top fixed priority owns the alarm; the AI runs below it and is treated as untrusted.

We used AI coding agents for much of the code. Most of our time went into deciding what to build and checking the results against real data.

## Challenges we ran into

- Our Hormuz receivers only hear a handful of ships, so the live stories come from the Baltic and Hormuz is told with satellite radar.
- Three of the first four "sanctioned" ships we found were on a port-inspection list, not a sanctions list. We now only count sanctions and shadow-fleet lists.
- For hours our live numbers read zero while every request still returned success. The data had stopped arriving. A small script now checks the actual values every few minutes.
- On the Pi, giving the AI a higher priority than the camera froze the camera, and the safety loop was stalling on its own disk writes. Fixing the order took us from 10 of 27 alarms on time under overload to 63 of 64.
- On a stormy night the sea was so bright on radar that our detector treated most of it as land.

## Accomplishments that we're proud of

- The Pi lookout holds its deadline under full CPU overload, and it tells you when it's blind instead of failing silently.
- We caught a mistake in our own Hormuz check: our Hormuz area contained the Gulf of Oman area we were comparing it to. After fixing it and re-running everything, the result held and got slightly stronger.
- The physics clears ships as well as flagging them. Ten ships went silent far out at sea and reappeared together a day later near the UAE, a trip that needed about 29 knots. They can't go that fast, so their positions were faked, probably by GPS jamming, and we don't flag them.
- We run our own ship detection on raw Sentinel-1 radar images.

## What we learned

Start collecting data before the idea is finished; live data only exists if you were listening. Check the actual numbers, not just whether the system says it's healthy. And the moments when the data disagreed with us turned into our best findings.

## What's next

More regions and better receivers, so the live part works in places like Hormuz too. And a lookout on an actual patrol boat.

## Prize tracks

**Best Use of QNX.** The lookout runs on QNX 8 on a Raspberry Pi 5 with a webcam, using the OpenCV DNN module from QNX's open-source repository with YOLOX-nano. Reliability is the point: it must alarm within 200 ms and must never go silently blind. A safety loop at the top fixed priority owns the decision and the AI runs below it, untrusted. If nothing arrives for 800 ms the screen says "lookout blind", and a standby AI takes over in about 150 ms. Under a 4-core overload, 63 of 64 alarms met the deadline, against 10 of 27 before we fixed the priorities. Sightings go to a hash-chained log on QNX's power-safe file system. We'll demo it on the Pi at the table.

**[MLH] Best Use of Tiger Data.** Tiger Data is our live store. Every position our recorder hears streams into a hypertable (over 590,000 rows by Sunday morning), and a per-minute continuous aggregate drives the live "position reports a minute" on screen. We had to turn on real-time aggregation: without it the newest minutes weren't summarised yet and the live rate read zero. The same database holds our Hormuz radar history.

## Attribution

Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.

## Built with

python, fastapi, typescript, vite, maplibre, deck.gl, timescaledb, tiger-data, postgresql, qnx, raspberry-pi, opencv, yolox, global-fishing-watch, sentinel-1, microsoft-planetary-computer, opensanctions, aisstream, pytest, hypothesis

## Links

- GitHub: https://github.com/kassem-kanjo/Hack-Western
- DEV post: https://dev.to/kassemkanjo/dark-fleet-we-spent-hack-western-chasing-ships-that-switch-off-their-transponders-2p9
- Video: (after YouTube upload)
