# Demo script v3 (2:30, expo table) — which ships went dark, and who they could have met

One analyst question: "Did any ship I care about go dark or meet someone, and where?" Five beats, one idea
each. Numbers in {braces} are read live by presentation mode from the snapshot; never say a number that is
not on screen.

| # | Time | Screen (route) | Say (plain, first person) | Proof on screen |
|---|---|---|---|---|
| 1 | 0:00 | `#/hormuz` | "Every big ship broadcasts where it is. Since the war began on 28 Feb, 2 in 3 ships that satellite radar sees here don't." | hero 66% (was 32%); "Gulf of Oman next door: 26% → 46%"; "Difference +14.5 pts (95% CI 2–26)"; two lines, 28 Feb marker |
| 2 | 0:20 | `#/hormuz` (story cards) | "SEA 1, on a shadow-fleet list, switched off for 13.1 hours in the Gulf of Oman, nine days after the strikes. It came back 96 km west, closer to the strait." Then: "On 12 March, 10 ships stopped broadcasting within two hours, far out in the Arabian Sea, and came back at one spot off the UAE. That trip needs 29 knots. They can't do that, so their positions were faked, not hidden." | SEA 1 card (13.1 h, 96 km west); cluster card: 10 ships, 1.7 h, up to 1,334 km, 29 kn, dashed false tracks on the map |
| 3 | 0:45 | `#/watch` (opens on the top story), then `#/watch/alert/{alert_id}` | "This one is live, from our own receivers in the Baltic: a ship went dark for {dark_duration}; a sanctioned tanker was within reach. Click: here's where it could have gone, and who it could have met." | figures "dark for / could be this far / ships it could have met"; reach shape on the map; could-have-met table |
| 4 | 1:20 | `#/patrol` | "When a transponder is off, you need eyes. This is a QNX Raspberry Pi with a camera: an AI lookout flags a vessel in view that isn't on AIS, inside a 200 ms deadline. I'll overload the Pi … alerts still on time. On the same overload without our priority guard, only 10 of 27 alerts were on time; with it, 63 of 64. I'll kill it … the unit says LOOKOUT BLIND at once, and a standby takes over in about 0.15 s." | camera, AI and safety-core steps lit; stress: {alerts_on_time} alerts on time, p99 {alert_p99_ms} ms; A/B card: 10/27 vs 63/64; kill: LOOKOUT BLIND, standby in 0.15 s (130–157 ms measured on the Pi) |
| 5 | 2:10 | `#/watch` | "It's for compliance teams and coast guards: physics says what's possible, lists say who matters, and the edge unit catches what's dark." | the top story, one line |

## QNX sponsor table only (20 s, beat 4 only)

| Time | Screen (route) | Say | Proof on screen |
|---|---|---|---|
| 0:00 | `#/patrol` | "A camera and an AI lookout on a QNX Pi: it flags a vessel in view that isn't on AIS inside 200 ms. Overloaded without our priority guard, 10 of 27 alerts were on time; with it, 63 of 64. I'll kill the AI … LOOKOUT BLIND, and a standby takes over in about 0.15 s (130–157 ms on the Pi). Pull the plug and the log still verifies." | LOOKOUT BLIND; standby 0.15 s; evidence log {chain_verifies} |

## Rules
- Rehearse on the snapshot you will show. The top story and the counts change as the recording grows.
- Wifi off for one full run (snapshot plus the offline basemap).
- One idea per beat. If a beat runs long, cut the proof, not the sentence.
- Say "every big ship broadcasts" and "radar sees"; never say "every ship must broadcast", and never call the 12 March ships
  "hiding": their positions were corrupted (29 kn needed), so we do not rank them.
- Never say "right now" for the watchlist, "six hotspots", or "AI-powered".
- If a number is not on screen, it does not exist.
- The Patrol beat needs the Pi on the table. At the QNX table use the 20-second variant. If the Pi is offline, show the A/B card and skip Kill/Overload.
