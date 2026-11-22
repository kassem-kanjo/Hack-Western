# STATUS (live)

Updated: Sat 21 Nov, 23:00 ET · HANDOVER for a new chat: read 'Handover' below first · V3 passed the 5-second test; Kassem reviewing · launch work paused until his 'happy' (except video + voice-over, which he asked for)

## Update (Sun 22 Nov, 04:00 ET)
- **Video live:** https://youtu.be/AwbrCT3uajE (on Devpost and the DEV post). Final files on the Desktop: v6 (Jessica AI voice) and v6B (friends' voice).
- **Still Kassem's:** Devpost Submit (tick terms) before 9:00; tracks on the live site if a page appears.

## Update (Sun 22 Nov, 02:50 ET) — morning, read first
- **Kassem, before 9:00:** (1) upload `Dark Fleet demo v5 (voiced).mp4` from the Desktop to YouTube (Unlisted) and send the link; (2) Devpost -> Dark Fleet -> Submit, tick terms, Submit project. Reminders fire 7:13 and 8:11.
- **After the YouTube link:** add it to Devpost (Video demo link) and to the DEV post (published: https://dev.to/kassemkanjo/dark-fleet-we-spent-hack-western-chasing-ships-that-switch-off-their-transponders-2p9).
- **Video:** v5 = friends' voice-over (Whisper-aligned, gaps tightened, 2:01) with footage re-cut to the voice in Remotion (media/video, composition DarkFleetV5, plan public/clips/v5plan_built.json). Old cuts are in ~/Desktop/Dark Fleet video - old versions.
- **Judging 12:00-1:00:** table script docs/demo-script.md; show the kill-the-AI moment live at the QNX table (not in the video). Re-run `scripts/freeze_case.py` just before judging if the story case should be fresh.
- **After judging:** public repo cleanup, issue #296.

## Update (Sun 22 Nov, 00:10 ET) — latest
- **Merged tonight:** #271 Hormuz re-measure (32% -> 68%, +15.8 pts), #277 Satellite check, #280 Patrol cut to essentials, #281/#283 Watch sanctions lines + legend, #285 "Follow one ship" story (case LAVENDER), #276 write-up/table script/voice-over.
- **Live:** :8000 runs main except /api/story (needs a restart after the video capture; then the story is live). Pi redeployed with BLIND 0.8 s; ship-photo test passed.
- **Devpost:** filled and saved as DRAFT (story, tags, GitHub, QNX + Tiger tracks, Western University, hacker ID keen-ibis-arrow). Kassem must tick terms + Submit before 9:00 (auto-mode blocked agreeing for the team). Reminders at 7:13 and 8:11.
- **Video:** Remotion, 82 s, captions = voice-over lines for friends to read; final render running; files on the Desktop. Stills in media/stills/. Source still in the builder worktree (scripts/record_video.py, media/video/): commit after the render.
- **OpenCode is off for the night** (Kassem): Claude Sonnet/Opus builders only.

## Update (Sun 22 Nov, 00:15 ET) — read before the handover below
- **Judging is 12:00-1:00 PM** (not 10:00): 3 general judges x (3 min demo + 1 min Q), no slides; sponsor tracks judged separately. Devpost locks 9:00 AM. Full list: docs/submission.md.
- **#266 landed** (#271): Hormuz 31.9% -> 67.5%, +15.8 pts vs Gulf of Oman (CI 3.4-27.0). Live server restarted on it.
- **Kassem approved "follow one ship"** (#274): replaces the caption tour; it IS the table demo and the video. Builder running in a worktree (own route #/story, frozen real case, presenter-paced, offline-safe).
- **Kassem asked us to do the write-up and everything else**: Comms is writing docs/devpost-final.md, docs/demo-script.md, docs/voiceover.md. Kassem only: add himself as team member, upload the video, pick tracks (QNX, MLH Tiger Data, Most Adventurous) on BOTH Devpost and the live site, press Submit by 8:30.
- Order: land #268 (Satellite check) -> #270 (sanctions line on Watch) -> #274 story -> 5-second test -> video from the story's autoplay -> screenshots -> Devpost final.

## Handover (Sat 21 Nov, 23:00 ET) — start here in a new chat

**Running when this was written (two builders, local worktrees, not pushed yet):**
- #266 Hormuz comparison areas overlap: the Gulf of Oman control box sits inside the 'Hormuz' box. Builder re-measures Hormuz without the control area, re-runs the validation (scripts/hormuz_validation.py), draws both areas on the map (red/grey), updates insights 16 + Devpost numbers. Worktree: .claude/worktrees/agent-ab1ad892e5602726c (branch worktree-agent-ab1ad892e5602726c). If the builder is gone: check its commits there, gate (pytest, ruff, tsc/build), cherry-pick onto a branch off origin/main, PR, merge on green. Headline numbers (66%, 32%, +14.5) may change: use only the new ones.
- #268 Satellite check: click a dark ship -> newest real Sentinel-1 pass over its reach area, detections matched to our AIS, not-broadcasting in red with chips. Worktree: .claude/worktrees/agent-a80e7eaa7f10db212 (branch worktree-agent-a80e7eaa7f10db212). Same landing steps. If no real pass caught a dark ship, Kassem approved a clearly labelled 'Scenario' (banner: 'Scenario: how an alert looks when a satellite pass catches a dark ship'), built from real parts, never mixed with live numbers.

**Next, in order:**
1. Land #266 and #268 (gate, PR, merge, rebuild web, restart nothing else).
2. Fresh-agent 5-second test on the result (first visit, 1280x720, no narrator; hide the camera image in Patrol shots).
3. Kassem reviews V3 (tour: P on Hormuz, arrows). Launch material only after he says he's happy.
4. Pi: redeploy with the 0.8 s BLIND threshold (#256 merged): `bash` the deploy in docs/patrol-demo.md with .env loaded (EDGE_STATUS_TOKEN). Only when Kassem isn't using the Patrol screen; ~1 min downtime. Then his ship-photo test (#237's last item).
5. Demo video: scripts/record_demo.py (Present mode, 1280x720 x1.5; on the Patrol beat use 'Show a recorded sighting', never live camera frames with people); encode with the ffmpeg bundled in PyPI imageio-ffmpeg (`uv run --with imageio-ffmpeg python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"`). Voice-over: docs/voiceover.md (replace [check] numbers after #266).
6. Devpost: docs/devpost-v2-draft.md ('we/our', no slogans); Kassem submits by 08:30 ET Sunday.

**Open decision for Kassem:** live alerts are Baltic-only because the coverage check needs 150 reports/min (Malaysia ~45, Hormuz ~2). Option: per-region coverage relative to each region's own normal rate, tested on Malaysia data first. Not started.

**Running processes (laptop):** recorder + scripts/recorder_watchdog.sh; build_watch.py --loop 60 --hours 12 (from main); tiger_sink.py --loop 30 under a bash restart loop; uvicorn on :8000 (EDGE_HOST, EDGE_DEMO_CONTROLS, DARKFLEET_DATA, DARKFLEET_WEB_DIST; .env loaded); scripts/sentinel.sh --loop 300 -> logs/sentinel.events. Restart processes by PID, never pkill -f a pattern a supervisor loop also matches.

**Rules learned tonight (also in memory/skills):** Kassem's sign-off before any launch material; public copy in 'we/our'; plain words (docs/copy-rules.md); every UI change passes the 5-second test; captions only state what is on screen; never fabricate data (labelled scenarios OK).

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
Sat 21 Nov, 19:52 ET: QA pass 2 fixes (#236 Watch/Hormuz polish, #238 Patrol buttons + plain labels + Present docking), CP3 review fixes (#239: Tiger fallback + resilient sink, risk-tagged Hormuz list 4->1, collapsed sightings, Pi hardening), Present live beat only shows real switch-offs (#240). CP3 verdict: security clean. Open: real ship-photo sighting on the Pi.
Sat 21 Nov, 19:21 ET: QNX v2 (#223: alarms on time 10/27 no guard vs 63/64 with guard on the Pi), evidence log on qnx6 (#224), Hormuz story cards (#225), demo v3 + Devpost (#226), Patrol sightings on Watch (#228), QA pass 1 fixes (#230, #232, #233), Evidence off the menu (#231). Copy rules in docs/copy-rules.md. Known, not fixed (freeze): live map stream caps 200 items/tick and sends no Malaysia/Black Sea positions.
Sat 21 Nov, 18:26 ET: V3 merged in slices (#210 Watch, #211 Hormuz demo view, #212 4-week line + legend, #213 opens on the strongest story). Tiger is the live store (#216 sink + ais_minutely, #218 real-time + 10-min rate; 450k+ pings, Baltic ~390 msgs/min). Sentinel (#209) watches every data path every 5 min; health retries flaky tests (#214). Kassem approved: Hormuz story cards (#219: SEA 1 dark 13.1 h in the Gulf of Oman; 20-ship outage cluster) and Patrol 1-4 (#220: lookout deadline, BLIND fail-safe, sightings to Watch, power-safe evidence log).
Sat 21 Nov, 17:40 ET: merged #199 (dossier, search, replay, area stats; per-file recording cache), #201 (own Sentinel-1 ship detection: 35 ships on the 21 Nov 02:06 UTC pass, 0 matched because our Hormuz recording started 6 h later), #202 (deploy kit), #204 (Hormuz trend API: 31.6% -> 66.0%, +14.5 pts beyond control), #205 (Patrol demo on the Pi: recovery 0.15 s, safety loop p99 1.5 ms under stress). Kassem approved the redesign mockups ('think of bloating'); builder running on #200 incl. trend panel + tour gate. Vultr: $100 credit applied; deploy blocked until Kassem links a payment method. Laptop died ~16:43: ~36 min recorder gap.
Sat 21 Nov, 16:23 ET: Watch V3 (#192), IMO-only sanctions (#189), Evidence screen + blank case fix (#195), toasts + Present tour + validation tooling (#193), Hormuz validation (#194: Hormuz 32% → 66% dark, +14.5 pts beyond Gulf of Oman control, CI +2 to +26; ceasefire drop; insight #16). Running: designer (Opus) mockups, Pi builder, satellite builder, integration of dossier/search/replay/areas into V3. Next: Hormuz screen shows the validated 2-year trend; tour uses the regime result; QA pass; CP3 review.
Sat 21 Nov, 16:00 ET: external Opus audit (scores 4-8; verdict: deep engine, app undersold it). Fixed: sanctions labels from exact IMO only (#189; 113 → 16 ships, 12 alerts). Hormuz 2-year radar series (475 days, in Tiger) shows not-broadcasting share 21-30% in 2025 → 61-72% Mar-Jun 2026, coinciding with the 28 Feb 2026 strikes and strait closure (35 sourced events in data/research/hormuz_events.json); unvalidated until the control-area test (#188). Running: Watch builder (nav, Hormuz view, live motion), Pi builder (sub-second recovery, real detection), satellite builder (own Sentinel-1 detection), lanes #182-#186, #188, #190.
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
