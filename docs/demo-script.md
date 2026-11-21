# Demo script v0 (3:00, expo table; every number from a committed run)

Story: **"Ships go dark. We work out where they could have gone, check the satellites, and clear the false
alarms, so an analyst only looks at what's left."** One story (Investigate). Numbers below are from
docs/region-scan.md, docs/rerank.md and (pending) docs/validation.md.

| Time | Screen | Say (plain, first person) |
|---|---|---|
| 0:00 | World map, 5,624 dots | "Every ship is supposed to broadcast where it is. In three months, 5,624 times a ship at sea went silent for 6 to 96 hours." |
| 0:20 | Funnel strip: 5,624 → 109 → 19 | "We check two things: where could it physically have gone, and did a radar satellite see something there. 109 had radar nearby. Physics and the satellite's actual pass time clear 90 of them." |
| 0:45 | Case file, a ruled-out case | Scrub the timeline: lens grows and shrinks; radar dot lands outside the lens at its pass time. "This one looked suspicious. The satellite passed 4 h 46 min after the ship was back on. Cleared, with the reason." |
| 1:30 | Case file, the best of the 19 | "This one we can't explain: the radar saw something exactly where the ship could have been, at that moment." |
| 2:00 | Validation panel | "Is this better than chance? <null ratio from #27>. And when we hide a ship we know, we find it <recall> of the time." |
| 2:30 | Who it's for + caveats | "For compliance teams and coast guards drowning in alerts. Free non-commercial data; radar misses small boats; speeds are class defaults." |
| 2:50 | Close | "Ask me about any dot." |

Open questions: which ruled-out case is the clearest (pick from docs/rerank.md "instructive" list); fill
validation numbers; QNX edge only at the QNX sponsor table (Sun 10 AM), not here.
