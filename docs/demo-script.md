# Demo script (one judge: 3:00 demo, 1:00 questions)

Press Right between beats. Placeholders: [SHIP], [LISTED SHIP], [LIST] are filled in tonight by the script.

## Opener (0:00)

"Dark Fleet finds ships that switched off their transponder, shows where they could have gone and who they could have met. It is for sanctions analysts and coast guards."

## Beats

**1. Switch-off (0:10-0:35).**
"A transponder is the radio that broadcasts a ship's position. This is [SHIP], in the Baltic, from our own live recorder. It just stopped broadcasting."
If it breaks: "The live feed is not showing it. We recorded it earlier; here is the saved case, and the recorder is still running."

**2. Where it could have gone (0:35-1:00).**
"The circle is everywhere it could have gone since, at its top speed. The clock shows how long it has been silent."
If it breaks: "The map did not draw. We tried a reload; the numbers are in the side panel."

**3. Who it could have met (1:00-1:30).**
"These ships were inside that circle. We say could have met, not met. This one, [LISTED SHIP], is on [LIST], matched by its permanent ID number, not its name."
If it breaks: "The match did not load. We tried the cached list; here is the ID number and the list entry on paper."

**4. Satellite pass (1:30-1:50).**
"[after #268: real pass or labelled scenario.] Radar sees ships that are not broadcasting."
If it breaks: "The pass view did not load. We tried the snapshot; here is the scene time from our records."

**5. The Pi (1:50-2:40).**
Turn to the Pi. Hold a ship photo to the camera.
"This is a Raspberry Pi running QNX. It sees a ship and no ship is broadcasting nearby, so it raises an alarm within 200 milliseconds."
Kill the AI.
"Now the AI is dead. The screen says LOOKOUT BLIND, not silence. The standby takes over in 130 to 157 milliseconds."
"Under full overload, 63 of 64 alarms landed on time. Before we fixed the priorities, 10 of 27."
If it breaks: "The camera did not respond. We tried a restart; here is our recorded test: 63 of 64 on time."

**6. Scale (2:40-3:00).**
"In the Strait of Hormuz, 2 in 3 ships seen by radar were not broadcasting from March to June 2026. In 2025 it was 1 in 3. The Gulf of Oman next door rose less, by 16 points (95% CI 3 to 27)."
If it breaks: "The chart did not load. The figures are 32% in 2025, 68% in March to June 2026."

## Questions (3:00-4:00)

1. **Is this just bad reception?** Sometimes. A silence only counts if our receivers were hearing that area, and Hormuz is compared with the Gulf of Oman beside it. Hormuz rose 16 points more.
2. **What about faked positions?** Physics catches them. On 12 March, 10 ships needed about 29 knots to reappear where they did. None can sail that fast, so we cleared them.
3. **Why QNX?** The alarm has a 200 ms deadline and the lookout must never go silently blind. A real-time system lets us set priorities and measure the deadline: 63 of 64 on time with them.
4. **What does Tiger Data do?** It stores every position our recorder hears in a hypertable (over 590,000 rows). A per-minute continuous aggregate gives the live rates on screen.
5. **Who would pay?** Sanctions compliance teams and coast guards. Our data is non-commercial; real clients would need commercial transponder and radar feeds.

## QNX sponsor variant (60 s)

"The lookout runs on QNX 8 on a Raspberry Pi 5. It uses QNX's open-source AI module: OpenCV DNN with YOLOX-nano. The use case is a lookout that must raise an alarm within 200 ms and never go silently blind. A safety loop at the top fixed priority owns the decision; the AI is untrusted. Kill the AI and the screen says LOOKOUT BLIND; a standby takes over in 130 to 157 ms. Under a 4-core overload, 63 of 64 alarms met the deadline, against 10 of 27 before we fixed the priorities. Putting the AI above the camera froze the camera. Sightings go to a hash-chained log on the qnx6 partition. Adaptive partitioning is not in this image, so we used fixed priorities."

## Tiger Data variant (45 s)

"Tiger Data is our live store. Every position our recorder hears streams into a `positions` hypertable: over 590,000 rows by Sunday 00:20 ET. A per-minute continuous aggregate gives the live position reports a minute on screen. The same database holds 475 days of Hormuz radar, 25,836 radar cells and 566 encounter events. Our store once looked alive for 8 hours while nothing new arrived, so we now check values, not status codes."
