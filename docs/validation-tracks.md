# Validating the Baltic engine on our own AIS tracks

Generated: 2026-11-21T15:27:59+00:00
Seed: 7 (fixed, so every number below is reproducible)
Source: aisstream files in ais/BALTIC_WATCH

## What we measured

- We use the AIS positions our own recorder already received; no external dataset is consulted and nothing is fetched.
- For each track we hide random stretches between 1 and 12 hours long in which the ship was underway (speed at least 2 knots). The last ping before a stretch and the first one after it stay visible, and every ping inside is the ground truth.
- Containment asks whether the space-time prism built from those two visible fixes covers every hidden fix: once anywhere in the prism, and once inside the lens at the hidden fix's own time. A window counts only if all its hidden fixes are covered.
- Bridge AUC scores each hidden path with the Brownian bridge and compares it with the same paths displaced 20 to 80 km at random, 20 decoys per window. AUC 1.0 means the true path always ranks first; 0.5 means the bridge cannot tell them apart.
- Gap recall cuts 1 to 6 hour stretches out of clean tracks, then runs the silence detector on what is left. A cut counts as found when a reported silence covers at least half of it.
- False positives run the jump and clone rules on the same clean tracks and count events per vessel-day, where vessel-days are the summed observed time span of every track.

## Numbers

| Measure | Value |
| --- | --- |
| Positions read | 271,535 |
| Vessels with enough pings | 300 |
| Masked windows scored | 66 |
| Hidden windows inside the whole footprint | 63 / 66 |
| Hidden windows inside the lens at each hidden time | 62 / 66 (93.9%, 95% interval 85.4% to 97.6%) |
| Bridge AUC against 20 decoys 20-80 km away | 1.000 (95% interval 1.000 to 1.000) over 66 windows |
| Bridge sigma used | 6.17 km/sqrt(h) |

### Silence-detector recall on injected gaps

| Measure | Value |
| --- | --- |
| Tracks with injections | 200 |
| Gaps injected | 512 |
| Gaps found | 43 |
| Recall, all injected gaps | 8.4% |
| Gaps starting while underway (>= 2 kn), the rule's scope | 83 |
| Found among those | 43 |
| Recall within scope | 51.8% |

### Jump/clone false positives on clean tracks

| Measure | Value |
| --- | --- |
| Clean tracks | 200 |
| Events fired | 1 |
| Vessel-days observed | 87.1 |
| Events per vessel-day | 0.01 |

## Caveats

- The ground truth is our own recorder's view of the ship. A transponder that reports a wrong position (spoofing or a bad fix) makes the truth wrong too; this harness cannot separate that from a geometry error.
- Masking a window and asking the prism to cover it tests the physics and the prism, not whether a real ship that goes dark follows the bridge. The hidden pings lie on the route the ship actually took, which is the most favourable possible case.
- Speed caps are the working assumptions in core.speeds, not measured service speeds, and a missing static message means the generic cap is used for that vessel.
- Bridge sigma is fitted on the same masked windows it is then scored on, so the AUC is optimistic in the statistical sense. It answers 'does the bridge rank the true path above displaced ones', not 'will it generalise to an unseen gap'.
- Injected gaps are uniform in time, while real AIS outages cluster near ports, coastlines and busy straits; recall here is an upper bound on real-world recall.
- Detection metrics need the silence/jump/clone rules from core.kinematics and the receiver coverage series. When either is absent the report says so instead of printing a zero.
- Tracks are thinned to one ping per interval before masking so a dense recorder does not produce thousands of hidden fixes per window; the thinning keeps the first ping after every real outage.

Data: our own AIS recorder. No Global Fishing Watch data is used in this report.
