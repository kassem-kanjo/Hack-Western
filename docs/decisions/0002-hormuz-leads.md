# Decision 0002: the Strait of Hormuz leads the demo

Status: **decided by Kassem** (Sat 21 Nov, 03:53 ET). Engine stays region-agnostic; the Baltic becomes "it works anywhere" and
our densest validation source.

## Why
The story: Iranian oil, sanctions evasion and ship-to-ship transfers off Fujairah/Khor Fakkan are what
compliance teams, insurers and navies care about. Our assets there: GFW radar is richest in the Gulf of Oman
(5,551 unmatched detections in Oct 2026); OpenSanctions lists 227 Iran-linked vessels, all with IMO numbers
(192 sanctioned), so live static reports can be matched by IMO, a hard identity.
GFW itself records almost no Hormuz "dark" events (1 gap in 6 months: its 50 nm offshore rule), which is the
gap our own live detection fills.

## The Hormuz view (three layers, honest about each)
1. **Live AIS (our recorder, since 03:51 Sat):** silences, jumps, cloning, meetings, IMO watchlist. Coverage
   check 04:53 decides how much weight this layer can carry.
2. **The unseen fleet (GFW radar):** per day, how many ships satellites saw vs how many were broadcasting,
   over 3 months, as a time series (Tiger Data continuous aggregate) and a density map. Aggregate, not an
   accusation of any one ship.
3. **History (GFW events):** encounters and loitering near the Fujairah anchorage.
Stretch: our own ship detection on fresh Sentinel-1 radar images of the strait (needs a free Copernicus account).

## Fallback
If live Hormuz AIS is too thin (< 50 vessels/hour), layer 1 shows the Baltic engine labelled as such and
Hormuz leads with layers 2-3.
