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
10. **A name is not an identity.** The sanctions join matched 7 of 109 cases by name; 5 were generic names
    (LISBOA, MARIA, PORTO, RIO GRANDE) that almost certainly belong to different ships. One, FU YUAN YU 7869,
    matches an OFAC SDN entry by name and hull number, but GFW's registry has no IMO for its MMSI and shows it
    once broadcast as "60658". Why it matters: screening tools that match on names flood analysts; ours
    separates strong from weak matches and says when identity is unconfirmed.
11. **GPS spoofing shows up as a shared impossible point.** In 5.5 h of our own Baltic recording, five unrelated
    ships each "jumped" 49 km from their real route to the same point on land near St Petersburg and back. A
    first, naive rule (many ships at one point) flagged 784 places, almost all ports; the physics rule (an
    impossible jump in AND out) left exactly one. Why it matters: GNSS interference is a navigation-safety
    problem in the Baltic, and the right test is kinematic, not statistical.
12. **Our clone detector was fooled by spoofing, and physics caught it.** Two "cloned identities" turned out to
    flip between their real track and the spoof point. Folding them into the spoof event made the claim
    narrower and truer.
13. **Robust numbers change the headline.** Hormuz "not broadcasting" share: mean 51% with thin days, median 41%
    over well-covered days. We publish the median and say which 25 days we dropped and why.
14. **A real ship-to-ship transfer is usually just refuelling.** The calibrated meeting detector found two real
    offshore tanker-cargo transfers in our recording: CSL THAMES + FOX SUNRISE off Gothenburg (about 1 m apart,
    40 min) and SPAR ARIES + CSM HYDRA in Singapore's anchorage (35 min). Both look like bunkering. Why it
    matters: shadow-fleet STS transfers hide among thousands of routine ones; location and watchlist context,
    not the transfer itself, are what make one worth a look.
15. **A detector's recall depends on what you ask it to find.** Cutting 512 random 1-6 h gaps out of our own
    Baltic tracks, the silence rule found 43 (8.4%). But it only claims gaps that start while a ship is underway
    (a moored ship going quiet is normal), and only 83 of the cuts did; within that scope it found 43 of 83
    (51.8%). Our space-time prism held the hidden route in 62 of 66 masked windows (93.9%). Why it matters: a
    single recall number without its scope can make a sound rule look broken, or a weak one look fine; the
    51.8% looked low, so we traced every miss (docs/recall-misses.md): in a later run all 37 in-scope misses fell
    inside minutes when our own recorder heard nothing (start-up and a restart), and the rule refuses to blame a
    ship for our outage. Where receivers were up throughout, it found 55 of 55. Source: docs/validation-tracks.md,
    docs/recall-misses.md (seed 7; the recording grows between runs, so counts differ slightly).
