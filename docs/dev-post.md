---
title: "Dark Fleet: tracking ships that switch off their transponders, and what the data taught us"
published: false
tags: mlhacks, devchallenge, hackathon, python
---

*This is a submission for the MLH DEV Writing Challenge, from Hack Western 13.*

## What We Built

Every large ship carries a transponder: the radio that broadcasts its position. Some ships switch it off, often tankers moving sanctioned oil to meet another ship at sea where nobody is watching.

Dark Fleet is for the sanctions analyst or coast guard officer who asks the same question every morning: did a ship we care about go dark, and who could it have met?

- **Watch (live):** we record transponder broadcasts from six regions. When a ship switches off, Dark Fleet draws everywhere it could have gone at its top speed and lists every ship it could have met inside that circle, flagging the ones on a sanctions list (matched by IMO number, not by name).
- **Satellite check:** for a ship that went dark, we pull the newest Sentinel-1 radar pass over its circle, run our own ship detection, and show in red the ships radar saw that weren't broadcasting.
- **Patrol unit:** a Raspberry Pi 5 running QNX with a camera. If it sees a vessel and nothing nearby is broadcasting, it raises an alarm within 200 ms. If its AI dies, it says "lookout blind" instead of going quiet.
- **Hormuz:** two years of satellite radar. 32% of radar-seen ships weren't broadcasting in 2025; 68% from March to June 2026, after the strikes. The Gulf of Oman next door rose too, and Hormuz rose 16 points more.

Two ships side by side with their transponders off is how sanctioned oil moves from one hull to another. We can't prove a meeting. We can show it could have happened, and where to look.

## Demo

- **Video:** [link]
- **Code:** https://github.com/kassem-kanjo/Hack-Western
- **Try it:** run the app, press **Follow one ship** (or P) and step through with the arrow keys. It walks one real case: a ship on a sanctions list goes dark in the Baltic, its reach circle grows, and two listed ships light up inside it.

## How We Built It

- A recorder takes one live transponder feed across six regions. A Python engine does the physics: how far a ship could have gone, whether two ships could have met, and whether a jump in position is even possible.
- Tiger Data (TimescaleDB) is the live store: every position streams into a hypertable, and a per-minute continuous aggregate drives the live rates on screen.
- Satellite radar from Global Fishing Watch, plus our own detection on Sentinel-1 scenes from Microsoft Planetary Computer.
- FastAPI behind a TypeScript front end with MapLibre.
- On the Pi: QNX 8, a small C helper for the camera, and YOLOX-nano through OpenCV's DNN module from QNX's own package repository. A safety loop at top priority owns the alarm; the AI runs lower and is untrusted.

## What the Data Taught Us

Most of what we learned came from the places where our own data or our own code proved us wrong.

### Our validation compared a region with part of itself

We measured how many ships seen by satellite radar were not broadcasting in the Strait of Hormuz. To make sure the rise was not just regional jamming, we compared it with the Gulf of Oman next door.

Late on, we found that our Hormuz box contained the Gulf of Oman area. The control was 5.8% of the box's radar detections (9,508 of 165,272). That bias pushes the gap toward zero.

We re-ran it with a strait-only area. The gap grew from 14.5 to 15.8 points (95% CI 3 to 27), the direction the bias predicted. Hormuz went from 31.9% to 67.5%. We published the corrected figure.

For anyone publishing a before-and-after chart: a control area only helps if it is really separate from the area it controls.

### Physics clears ships as well as flagging them

In Global Fishing Watch's gap events, 10 ships stopped broadcasting within 1.7 hours on 12 March 2026. They were last seen up to 1,334 km out in the Arabian Sea and reappeared about 22 hours later at one spot off the UAE.

That trip needs a median 29 knots. All 10 exceed their class speed cap of 16.1 knots. Their reported positions were faked, most likely by GPS interference.

A simple "went quiet" detector turns one interference event into ten suspects. A speed check turns it into one finding. We do not rank those ships as hiding.

### "On a list" is not "sanctioned"

We matched gap events against the OpenSanctions maritime list by ship ID and got four ships. Three of them appeared only in the Tokyo MOU port-state-control data, which records port inspections and carries no risk tag. Only one, SEA 1, carried a shadow-fleet tag.

Our first count was inflated. We now count only ships on sanctions or shadow-fleet lists. A compliance tool has to say which list a ship is on and why.

### A real-time operating system is not enough on its own

Our patrol unit is a Raspberry Pi 5 running QNX with a camera. It must raise an alarm within 200 ms.

We loaded all four cores for 10 seconds. Without our priority guard, 10 of 27 alarms met the deadline. With it, 63 of 64 did. The safety loop's worst delay fell from 24 ms to 1.8 ms.

Two things surprised us. Giving the AI a higher priority than the camera froze the camera after 3 frames. And the safety loop's worst stalls, up to 270 ms, came from its own disk writes, not from CPU load. Moving the writes off the loop fixed it.

The operating system gave us the tools. Where each task sat in the priority order, and what blocked inside the loop, decided whether the deadline held.

### A store that loaded once looks alive for hours

Our live database held 265,250 position reports from one morning load. For about 8 hours nothing new arrived, and every screen still answered with a success code.

After we connected a live feed, the area rates read zero everywhere, again with every route returning success. The newest minutes had not been summarised yet, and quiet regions only update every five minutes.

Only checking values caught it. Checking that the service was up did not.

### Satellites are rarely overhead at the right moment

We added a satellite check: for a ship that switched off, find the newest Sentinel-1 radar pass over the area it could have reached, detect every ship in it, and match them against the broadcasts we heard. Red means radar saw a ship that wasn't broadcasting.

Of the first five live Baltic cases, three had no pass in the 48 hours around the switch-off. The two that did were 17 hours early, so neither shows the ship while it was dark. The tool says that plainly instead of implying a catch.

The detector also surprised us. On a stormy night the radar-bright sea tripped our land test, which blanked about 95% of the water and found zero ships in a scene with dozens of raw detections. Bright-sea scenes now skip that test and rely on the coastline.

## What's Next

We want more regions and more receivers. Our Hormuz receivers hear only about 17 ships, so live stories there are not yet possible. We also want to put the lookout on a real patrol boat. Commercial transponder and radar feeds would let us serve real clients; ours are non-commercial.

## Hackathon Experience

We built this in 36 hours at Hack Western 13. AI coding agents wrote most of the code in parallel lanes; our job was deciding what to build, checking every change on real recorded data, and throwing out what didn't hold up. The best moments were the ones where the data disagreed with us, which is why this post is mostly about those.

Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.
