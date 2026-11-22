---
title: "Dark Fleet: we spent Hack Western chasing ships that switch off their transponders"
published: false
tags: mlhacks, devchallenge, hackathon, python
---

*This is a submission for the MLH DEV Writing Challenge, from Hack Western 13.*

On Friday night at Hack Western 13, before we had a name or a single screen, we started recording every ship broadcasting its position in six parts of the world. We didn't know exactly what we'd build yet. We just knew that live ship data only exists if you were listening when it happened.

The problem we wanted to work on: big ships carry a transponder, a radio that tells everyone where they are. Some switch it off, often tankers moving sanctioned oil to another ship at sea, where nobody can see the transfer. Sanctions teams and coast guards have to work out where those ships went and who they met.

## What We Built

Dark Fleet. When a ship goes quiet, it shows everywhere the ship could have gone since, which other ships it could have met, and whether any of them are on a sanctions list. Then it helps you look: a satellite radar check over the area, and a lookout on a Raspberry Pi running QNX that raises an alarm when it sees a boat that isn't broadcasting.

## How It Actually Works

**Step 1: record.** One connection to a live transponder feed covers six regions. Every position goes to disk and into Tiger Data, where a per-minute continuous aggregate drives the live numbers on screen.

**Step 2: notice a real silence.** A ship stopping is easy to spot. Knowing it means something is harder: we only raise an alert if our own receivers were hearing that area at the time. Otherwise it's a gap in coverage, not a ship hiding.

**Step 3: draw the circle.** From the last position and the ship's top speed, we draw everywhere it could be by now. Then we check every other ship's own track to see which ones could actually have been in the same place at the same time. In one live case, 129 ships were inside the circle and only 15 could have met it.

**Step 4: check the lists.** We match on the IMO number, which stays the same when a ship is renamed, and show which list a ship is on.

**Step 5: look from above, and from the water.** For the satellite check we pull the newest Sentinel-1 radar image over the circle and run our own ship detector on it. Radar sees ships whether they broadcast or not, so anything it sees that isn't broadcasting shows up red. On the water, the Pi's webcam feeds an object detector, but a small safety loop at QNX's top priority owns the alarm. The AI runs below it and is treated as untrusted. Kill the AI and the Pi says "lookout blind" instead of going quiet, and a standby takes over in about 150 ms.

## What Went Wrong (and What We Did)

**Our first sanctions hits weren't sanctions.** Three of the first four "listed" ships were on a port-inspection list. We now count only sanctions and shadow-fleet lists, and say which one.

**Everything said OK while nothing was working.** For hours our live numbers read zero and every request still returned success. The data had stopped arriving. Now a small script checks the actual values every few minutes.

**The camera froze.** We gave the AI a higher priority than the camera, and the camera stopped after three frames. Getting the order right was the biggest win of the weekend: under the same overload test, 10 of 27 alarms met the 200 ms deadline before, and 63 of 64 after.

**Our own validation had a hole.** For the Strait of Hormuz we used two years of satellite radar and found the share of ships not broadcasting roughly doubled after the war began. We'd compared it with the Gulf of Oman to rule out bad reception, and late Saturday night we realized our Hormuz area contained the Gulf of Oman area. We redrew it and re-ran everything. The result held, and got slightly stronger.

**Satellites are rarely overhead at the right time.** No pass has caught a live ship while it was dark yet. The closest came 17 hours early, and the app says that plainly. And on a stormy night the sea was so bright on radar that our detector treated most of it as land until we fixed the check.

**Our first guided demo didn't work.** We replaced it with a story that follows one real ship from the moment it switches off.

## What We Learned

The physics clears ships as well as flagging them. Ten ships went silent far out at sea and reappeared together a day later near the UAE, a trip that needed about 29 knots. They can't go that fast. Their positions were faked, probably by GPS jamming, so we don't flag them.

Start collecting data before you've finished the idea. And check the real numbers, not just whether the system says it's healthy.

## Tools: What Worked and Where We Hit Friction

**QNX.** The OpenCV DNN package from QNX's own repository ran our detector on the Pi without us building anything from source. Adaptive partitioning wasn't in the image we had, so we used fixed priorities instead, which in the end was the thing that mattered.

**Tiger Data.** Hypertables and continuous aggregates were quick to set up. The catch: the newest minutes aren't summarised yet by default, so our live rate read zero until we turned on real-time aggregation.

We used AI coding agents for much of the code. Most of our time went into deciding what to build and checking their work against real data.

Holding a photo of a boat up to the webcam just after midnight and watching the alarm go off was when it all felt real.

**Code:** https://github.com/kassem-kanjo/Hack-Western
**Video:** (link added after upload)

*Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.*
