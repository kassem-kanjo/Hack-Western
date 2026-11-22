---
title: "Our Hack Western weekend chasing ships that switch off their transponders"
published: false
tags: mlhacks, devchallenge, hackathon, python
---

*This is a submission for the MLH DEV Writing Challenge, from Hack Western 13.*

Big ships carry a transponder, a radio that broadcasts where they are. Some ships switch it off, often tankers moving sanctioned oil to another ship at sea where nobody can see it.

We built Dark Fleet for the people who track that: sanctions teams and coast guards. When a ship goes quiet, it shows everywhere the ship could have gone since, which other ships it could have met, and whether any of them are on a sanctions list. Here's how the weekend went and what we learned building it.

## Friday: start recording first

Live ship data only exists if you're listening when it happens, so the first thing we did on Friday night was start a recorder on six regions and leave it running. It has been running ever since.

That decision shaped the project. The Baltic gave us thousands of position reports every few minutes. The Strait of Hormuz, the place we cared about most, gave us a handful of ships. So the live part of the app runs on the Baltic, and for Hormuz we turned to satellite radar.

## Saturday: making the alerts trustworthy

The core idea is simple physics. When a ship stops broadcasting, we know its last position and its top speed, so we can draw a circle of everywhere it could be by now. Then we check every other ship's own track to see which ones could actually have been in the same place at the same time. Usually most ships in the circle get ruled out, and only a few could have met it.

Two things made this harder than expected. First, a silence only means something if our receivers were hearing that area at the time, so we only raise an alert when coverage was good. Second, sanctions matching: three of our first four "sanctioned" matches turned out to be on a port-inspection list, not a sanctions list. We now match on the ship's IMO number, which doesn't change when a ship is renamed, and the app always says which list a ship is on.

We also had a stretch where the live numbers read zero for hours while every part of the app reported success. The data had simply stopped arriving. Since then a small script checks the actual values every few minutes.

In the afternoon we started on the QNX part: a Raspberry Pi with a webcam that acts as a lookout. If it sees a boat and nothing nearby is broadcasting, it raises an alarm within 200 ms. A small safety loop owns that alarm, and the AI detector only feeds it. Getting the priorities right mattered more than anything: under the same overload test, 10 of 27 alarms met the deadline before we fixed the order, and 63 of 64 after. If the AI dies, the Pi says "lookout blind" instead of going quiet, and a standby takes over in about 150 ms.

## Saturday night: checking our own results

For Hormuz we used two years of satellite radar, which sees ships whether they broadcast or not. The share of ships not broadcasting roughly doubled after the war began this year. To make sure that wasn't just bad reception, we compared it with the Gulf of Oman next door.

Late that night we found a mistake: our Hormuz area contained the Gulf of Oman area we were comparing it to. We redrew the boundaries and re-ran everything. The result held, and got slightly stronger.

The same physics also cleared some ships. Ten ships went silent far out at sea and reappeared a day later at one spot near the UAE, a trip that would have needed about 29 knots. They can't go that fast, so their positions were faked, most likely by GPS jamming, and we don't flag them.

We also added a satellite check for live alerts: the newest Sentinel-1 radar image over the circle, our own ship detection on it, and a red mark for every ship the radar sees that isn't broadcasting. So far no pass has caught a ship while it was dark. The closest came 17 hours early, and the app says so.

## Sunday: making it demo-able

Our first guided tour of the app didn't work, so we replaced it with a story that follows one real ship: it switches off, the circle grows, and two ships on sanctions lists appear inside it. Just after midnight we held a photo of a boat up to the Pi's webcam and watched the alarm go off, which was the moment the whole thing felt real.

## What we learned

Start collecting data before the idea is finished. Check the actual numbers, not just whether the system says it's healthy. And treat it as good news when the data disagrees with you: our best findings came from those moments.

We used AI coding agents for much of the code. Most of our time went into deciding what to build and checking the results against real data.

**Code:** https://github.com/kassem-kanjo/Hack-Western
**Video:** (link added after upload)

*Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.*
