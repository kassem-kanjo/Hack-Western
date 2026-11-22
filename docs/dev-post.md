---
title: "Our Hack Western weekend chasing ships that switch off their transponders"
published: false
tags: mlhacks, devchallenge, hackathon, python
---

*This is a submission for the MLH DEV Writing Challenge, from Hack Western 13.*

Big ships carry a transponder, a radio that keeps telling everyone where they are. Some switch it off. Sometimes it's a broken radio. Sometimes it's a tanker meeting another ship in the middle of the sea to move oil that's under sanctions.

We wanted to build the thing someone in a sanctions team or a coast guard would open in the morning: which ships went quiet overnight, where could they have gone, and who could they have met. That became Dark Fleet. Here's how the weekend actually went.

## Friday night: just start recording

We didn't have a finished idea yet, but we knew we'd need data, and live ship data only exists if you're listening at the time. So the first real thing we did, around 11 PM, was start a recorder on six regions around the world and leave it running. It's still running as we write this.

The Baltic turned out to be the busy one, thousands of position reports every few minutes. The Strait of Hormuz, the place we actually cared about most, barely gave us anything. Our receivers there hear a handful of ships. That changed the whole plan: live stories would come from the Baltic, and for Hormuz we'd need satellites.

## Saturday: the data starts arguing back

On Saturday we matched the ships in our Hormuz data against sanctions lists and got excited for a bit. Four hits. Then we looked closer and three of them were on a list of port inspections, not sanctions. Not the same thing at all. Now the app always says which list a ship is on.

Later we noticed our live numbers had been reading zero for hours, while every part of the app said everything was fine. Nothing was crashing. The data just wasn't arriving. After that we wrote a small script that looks at the actual numbers every few minutes and complains if they stop moving.

In the afternoon we got a Raspberry Pi with QNX from the QNX table and turned it into a lookout: a webcam, an object detector, and a small safety loop that owns the alarm. The first time we put it under load, the camera froze after three frames. We'd given the AI a higher priority than the camera. Once we got the order right, the numbers flipped: under the same overload, 10 of 27 alarms made the 200 ms deadline before, and 63 of 64 after. Kill the AI now and the Pi says "lookout blind" instead of going quiet, and a standby takes over in about a seventh of a second.

Somewhere in there a laptop died.

## Saturday night: we were wrong, twice, in good ways

For Hormuz we used two years of satellite radar, which sees ships whether they broadcast or not. The share of ships that weren't broadcasting roughly doubled after the war started this year. To check it wasn't just bad reception, we compared it with the Gulf of Oman next door.

Late Saturday night we realized our "Hormuz" box actually contained the Gulf of Oman box. We'd been comparing a place with part of itself. We redrew it and re-ran everything, honestly expecting the finding to fall apart. It got slightly stronger. That was a relief.

The other surprise was ten ships that "switched off" within two hours of each other far out at sea and came back together a day later at one spot near the UAE. To do that they'd have needed about 29 knots, and they can't go that fast. Their positions were faked, probably by GPS jamming. So the same physics that flags suspicious ships also clears innocent ones, which we hadn't thought about going in.

We also built a satellite check for live ships, pulling the latest radar image over the area a dark ship could have reached. It hasn't caught one in the act yet; the closest pass was 17 hours early. And on a stormy night the sea got so bright on radar that our detector decided most of it was land. That one took a while.

## Sunday, after midnight

Our first guided demo was bad. Somebody looked at it and called it useless, and they were right, so we threw it out and built a story that follows one real ship instead: it switches off, a circle grows around everywhere it could be, and two ships on sanctions lists show up inside it.

Then, a bit after midnight, we held a photo of a boat up to the webcam and watched the alarm go red. Best moment of the weekend.

The demo video took three tries. The first one looked like a screen recording someone forgot to crop.

## What we'd tell ourselves on Friday

Start recording before you know what you're building. Look at the actual numbers, not whether the app says it's OK. And when your data disagrees with you, that's usually the interesting part.

We used AI coding agents for a lot of the code. Most of our time went into deciding what to build and checking their work against real data.

**Code:** https://github.com/kassem-kanjo/Hack-Western
**Video:** (link added after upload)

*Powered by Global Fishing Watch. Sanctions data from OpenSanctions (CC BY-NC). Radar from Copernicus Sentinel-1 via Microsoft Planetary Computer.*
