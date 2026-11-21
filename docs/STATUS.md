# STATUS (live)

Updated: Sat 21 Nov, 15:35 ET · next checkpoint: CP1 decisions from Kassem · plan: docs/plan.md v3 · briefing: docs/briefings/cp1-sat-morning.md

## In flight (overnight build, plan v3 + decisions 0001/0002)
- **Recorder:** one connection, six regions (Baltic, Hormuz, Malaysia EOPL, Laconia, Red Sea, Black Sea) since 03:54.
  Coverage per 5 min: Baltic ~3,700 msgs, Malaysia EOPL ~310, Black Sea ~100, Hormuz ~7, Laconia/Red Sea 0.
- **Story:** Hormuz leads with satellite radar + GFW history; live engine runs on Malaysia EOPL (Iranian-oil
  transfer hub) and the Baltic. Tabs: Global · Live · Patrol unit.
- **DeepSeek lanes running:** #95 kinematics calibration, #94 meeting calibration, #79 region pipeline,
  #80 live API, #88 Hormuz unseen fleet, #89 Hormuz history.
- **Next:** UI V1 (live screen + event detail + nav, per design/v2), then UI V2 (presentation mode,
  explainers, replay). CP2 Opus + security review once UI V1 lands.

## Engine wave 1 (merged, #93): first real-data findings (Baltic, 5 h, 107,818 pings, 3,751 ships)
- 17 sanctioned + 2 shadow-fleet vessels matched by IMO, sailing in the Baltic.
- A cloned identity: MMSI 630001188 reports from St Petersburg and 49 km north, alternating over 3 h.
- Silences need local coverage (#95); meetings were mostly moored ships in ports (#94).

## Waiting on Kassem (morning, ~2 min each)
Vultr sign-up (deploy + recorder off the laptop) · MLH codes (Entire, GoDaddy) into .env · QNX Pi by noon ·
optional: free Copernicus account (own radar ship detection, #90).

## Done (merged)
Sat 21 Nov, 15:35 ET V2: Watch screen (global map, analyst style, live 5-min alerts, trails, Hormuz radar alert) #171 #174 #177; Mac app #175 #176; security fixes #172; QNX: OpenCV DNN fallback #169, qnxcam #173, real-time priorities + stress #178; false-alarm rules #179. Running: Pi camera into pipeline (w30), Patrol demo screen (w31), rule wiring + freshness (w32). Then QA pass, CP3 review, Kassem reviews V2.
Sat 21 Nov, 14:50 ET: QNX Pi 5 online (Hack Western 13 wifi, key SSH); edge unit deployed and running on QNX with OpenCV DNN (#169). Open: detector heartbeat while waiting for network frames; webcam on the Pi. UI V3 Watch screen: core merged (#166), UI + pipeline on branch integ/wave22-ui, alert tuning in review. Security fixes lane running. Recorder watchdog added.
Story approved by Kassem (Sat 21 Nov, 13:09 ET): Hormuz, then live Baltic, then Patrol unit (decision 0002). QNX Pi 5 + webcam in hand; edge unit tested on laptop (#157); Pi needs wifi before deploy.
Sat 10:05-11:25: #148 validation on our own tracks (62/66 prism, 1 FP in 87.1 vessel-days), Black Sea empty state, Hormuz phone chart · #149 recall misses traced (all = our recorder outages; 55/55 where receivers up) · #150/#151 README hero + own-track validation · #153 thought piece #2 · 7 done issues closed.
Sat 09:20-10:05: #141 replay chips (#138), phone view, Vultr deploy kit (deploy/), Devpost draft · #142 Tiger loaded (265,250 pings) + hourly aggregate chart on Global, first-visit intro · #144 make demo-check (8/8 PASS), demo video v1 (media/out, gitignored), thought piece #1 (docs/thoughts).
UI V2: replay slider (#85) + 10 m coastline, snapshot-relative last seen (#134) merged in #137 (08:54). Open follow-up #138.
#1 scaffold · #2 #3 core prisms · #4 #6 data layer (Tiger Data: 6,049 SAR cells, 18 encounters) ·
#8 design · #9 research · #20 region scan (109 of 5,624 gaps with radar in prism) · #21 CI fix ·
plan v2.1 after Opus Auditor review · #29 API schema frozen.

## Key finding of the night
Auditor showed the planned opener (CL YICHUN) fails our own physics → validation (#27) is the headline.

## Blocked / waiting on Kassem
- Delete ~/Downloads/tiger-cloud-hack-western-credentials.env (plain-text password; copy now in .env).
- QNX Pi: ask around Saturday morning.
- MLH claim codes (Entire, GoDaddy): log in at mlh.com prizes page.

## Feasibility gates (docs/mission.md)
F1 GFW access: PASS · F2 SAR: PASS in Hormuz (0.01°, daily), Baltic empty · F3: Baltic FAIL, Hormuz PASS → region switched to Hormuz ·
F4 prism matcher: pending · F5 live AIS: Baltic PASS (591/min), Hormuz FAIL (2/90 s) → live layer = Baltic watchlist · F6 storage: PASS (Tiger Cloud, TimescaleDB 2.32) · F7 edge detector: pending
