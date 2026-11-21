# Insights log (what the build surprised us with)

Raw material for the DEV post and thought pieces. Each entry: what happened, evidence, why it matters.

1. **"Dark" depends on who's counting.** GFW only flags an AIS gap as intentional > 50 nm offshore. Result:
   0 gap events in the Baltic (Sep 1-Nov 15) and 1 inside the Strait of Hormuz in 6 months, the two places
   most associated with shadow fleets. Evidence: docs/feasibility-dark-fleet.md. Why it matters: conservative
   rules protect against false alarms but leave the busiest sanction-evasion chokepoints unwatched.
2. **Radar sees thousands of ships that say nothing.** Gulf of Oman, October 2026: 5,551 radar detection cells
   with no matching AIS (6,049 in the stored run). Coverage itself is uneven: 1 detection in the Gulf of
   Finland the same month. Why it matters: "no AIS" is normal, not exceptional, in some waters.
3. **Volunteer AIS receivers map geopolitics.** aisstream: 591 vessels/min in the Baltic, 2 vessels in 90 s
   around Hormuz. The live picture is only as good as who hosts receivers.
4. **Prisms need speed headroom to find meetings.** Two ships meeting exactly at their speed cap produce
   lenses that collapse to a point; detection depends on the speed margin (1.15x). Evidence: core test.
5. **109 gaps had an unexplained radar detection their ship could have reached.** Of 5,624 non-gear 6-96 h gaps (Aug 15-Nov 15), 109 had unmatched
   radar detections inside their prism. Best transit case: CL YICHUN (cargo), dark 22.9 h off Guyana, 2
   candidates. Evidence: docs/region-scan.md.
6. **Some "dark" fleets are probably gear.** Consecutive MMSIs 210700xxx (type insufficient_data) off Guyana go
   dark together: likely tag-like transmitters, a false-positive class worth filtering. Brazilian oil-field
   supply boats sit still near platforms, so radar "candidates" there may be the platforms themselves.
7. **Our best case failed our own physics.** CL YICHUN looked like the perfect opener until the Auditor did
   the arithmetic: reaching the radar dots needed 18.4 kn average vs 10.8 kn straight-line, and the dots were
   only reachable in a 75-minute window that doesn't match the satellite pass. Why it matters: a dramatic
   anecdote is not evidence; a tool that rules cases *out* is more trustworthy than one that only flags.
8. **Most alarms are innocent, and physics can prove it.** Of 109 gaps with radar inside their possible-reach
   area, 90 are ruled out: 59 never moved (off≈on), 16 had the radar dot outside the lens at the satellite's
   actual pass time, 10 couldn't reach any dot at their class speed, 5 had the only pass outside the gap.
   19 remain unexplained. Evidence: docs/rerank.md. Why it matters: compliance teams drown in alerts; a tool
   that clears 83% of them with a reason each is worth more than one that flags more.
9. **"Inside the prism" carries almost no information.** A position moved 50-150 km away still lands inside a
   typical 40 h prism 98.4% of the time. The Brownian bridge fixes localisation (AUC 0.87) but radar near dark
   ships still matches time-shifted chance (ratio 1.03). Why it matters: many public "radar caught a dark ship"
   claims may be background traffic; test against a shifted null before accusing anyone.
