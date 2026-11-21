# Validation: does radar in a dark ship's prism beat chance?

Generated: 2026-11-21
Live API requests: 79
The report compares observed dark-gap matches with shuffled null runs and ground-truth recall.

## Headline

| Measure | Value |
| --- | --- |
| Variant | All kept gaps, all unmatched radar cells (as specified) |
| Real matches | 108 |
| Shuffled mean (95% range) | 100.5 (86.5 to 117.3) |
| Ratio (95% interval) | 1.07 (0.91 to 1.25) |
| p-value | 0.235 |
| Interpretation | not distinguishable from chance (interval includes 1) |

| Measure | Value |
| --- | --- |
| Variant | Moving gaps only (off-on >= 5 km) and radar cells seen on < 3 days (plan item 3 filters, declared in advance) |
| Real matches | 49 |
| Shuffled mean (95% range) | 43.8 (35.0 to 54.1) |
| Ratio (95% interval) | 1.12 (0.88 to 1.37) |
| p-value | 0.137 |
| Interpretation | not distinguishable from chance (interval includes 1) |

Recall rates:

| Measure | k / n | Rate | 95% Wilson interval |
| --- | --- | --- | --- |
| True position inside the whole-gap footprint | 143 / 146 | 97.9% | 94.1% to 99.3% |
| Inside the day-lens (the radar-cell test we use) | 146 / 146 | 100.0% | 97.4% to 100.0% |
| Inside the lens at the exact instant | 140 / 146 | 95.9% | 91.3% to 98.1% |
| Chance: same test with the true position moved 50-150 km in a random direction | 2,874 / 2,920 | 98.4% | 97.9% to 98.8% |

## Method in plain words

- Take the AIS gaps the region scan kept (5,624 non-gear gaps of 6 to 96 hours; 1,338 of them sit in the 15 cells where radar was fetched and enter Test 1). For each gap the 'prism' is every place the ship could have reached given its last and next known position, with a class speed limit.
- A gap counts as a hit if at least one unmatched radar cell on a day of the gap lies inside that prism (radar cells carry a date, not a time, so we use the whole day's prism).
- Test 1 moves each gap's prism in time by 7 to 21 days (same place, same shape, same radar data) and recounts. If the real count is no higher than these shifted counts, the radar cells we found are what you would see in that water on any week, not a signal about the ship.
- Test 2 checks the prism itself: where a ship's position is known from GFW events between two other events, we pretend the first and last were the only fixes and ask whether the known position is inside the prism. We also move that position 50 to 150 km at random to see how often a wrong position would also be inside.

## Test 1: the null shuffle

| Measure | Value |
| --- | --- |
| Variant | All kept gaps, all unmatched radar cells (as specified) |
| Dark gaps tested | 1,338 |
| Real matches | 108 |
| Shuffles | 50 |
| Shuffled mean | 100.5 |
| Shuffled standard deviation | 8.8 |
| 95% null range | 86.5 to 117.3 |
| Ratio (95% interval) | 1.07 (0.91 to 1.25) |
| p-value | 0.235 |
| Interpretation | not distinguishable from chance (interval includes 1) |

| Measure | Value |
| --- | --- |
| Variant | Moving gaps only (off-on >= 5 km) and radar cells seen on < 3 days (plan item 3 filters, declared in advance) |
| Dark gaps tested | 1,121 |
| Real matches | 49 |
| Shuffles | 50 |
| Shuffled mean | 43.8 |
| Shuffled standard deviation | 4.6 |
| 95% null range | 35.0 to 54.1 |
| Ratio (95% interval) | 1.12 (0.88 to 1.37) |
| p-value | 0.137 |
| Interpretation | not distinguishable from chance (interval includes 1) |

Spatial shuffle: not done. Reason: Skipped: no land mask in the project and a 50-150 km spatial shift would need radar re-fetched around every shifted prism (the cached radar covers each 5x5 degree cell plus 1 degree only), and without a land mask shifted prisms that land on shore would bias the shuffled count downward.

- 1338 gaps in the 15 busiest 5x5 degree cells (the ones the region scan fetched radar for); the other 4,286 kept gaps have no radar fetched and are not in this test.
- Each gap's prism is shifted by a random whole number of days with 7 <= |shift| <= 21, drawn uniformly from the shifts that keep the shifted gap inside 2026-07-25 to 2026-11-15 (radar days we fetched). 50 shuffles; 0 gaps with no valid shift are left out.
- Shifts are whole days, so the shifted prism is the same shape and the same time of day; only which radar overpasses fall in it changes.

## Test 2: ground-truth recall

API probes:

| Request | Result |
| --- | --- |
| POST /v3/4wings/report public-global-sar-presence:latest, filters[0]=matched='true', group-by=VESSEL_ID | 404: 'This feature is temporarily unavailable because of infrastructure constraints and an upstream data dependency issue.' |
| same, group-by=MMSI | 404, same message |
| same, group-by=DETECT_ID | 422: option not valid for dataset public-global-sar-presence:v5.0 |
| same, no group-by (matched='true') | 200, rows carry date, lat, lon, detections and a vesselIDs COUNT (1), not an identity |
| same, group-by=FLAG or GEARTYPE | 200: adds the vessel's flag or class to each matched detection, still no identity |

Vessel attribution available: no
Next-best alternative: The API will not say which vessel a matched radar detection belongs to, so the real recall of 'prism plus radar finds the true ship' cannot be measured yet. Next-best truth used here: a vessel's own GFW event between two others (port visit, loitering, encounter) as the true position inside a synthetic gap. That tests the physics (speed cap, prism geometry), not radar visibility. Once 4Wings vessel attribution returns, rerun with matched detections as truth; or use a licensed AIS archive to hide a vessel's track and ask whether its radar blob falls in the prism.

| Measure | Value |
| --- | --- |
| Cases | 146 |
| Vessels | 66 |
| Median gap (hours) | 40.4 |

Recall rates:

| Measure | k / n | Rate | 95% Wilson interval |
| --- | --- | --- | --- |
| True position inside the whole-gap footprint | 143 / 146 | 97.9% | 94.1% to 99.3% |
| Inside the day-lens (the radar-cell test we use) | 146 / 146 | 100.0% | 97.4% to 100.0% |
| Inside the lens at the exact instant | 140 / 146 | 95.9% | 91.3% to 98.1% |
| Chance: same test with the true position moved 50-150 km in a random direction | 2,874 / 2,920 | 98.4% | 97.9% to 98.8% |

Recall by gap type:

| Gap type | n | Day-lens k |
| --- | --- | --- |
| fishing | 58 | 58 |
| other_non_fishing | 29 | 29 |
| inactive | 29 | 29 |
| passenger | 5 | 5 |
| insufficient_data | 16 | 16 |
| carrier | 3 | 3 |
| cargo | 6 | 6 |

## Caveats

- Result in one line: the real count (108 of 1,338 gaps) is 1.07 times the shifted-prism count (interval 0.91 to 1.25), so this data does not show that radar inside a prism beats chance. In the two Brazilian cells, which hold most of the hits, real and shifted counts are the same (33 vs 33.4 and 34 vs 35.4): those hits are traffic and structures that are there every week.
- Per-cell look (exploratory, not a tested claim): the three cells outside Brazil with hits show a small excess (14 vs 9.8, 18 vs 13.5, 8 vs 5.4 shifted); the sample is too small to tell it from chance.
- Test 2 does not show the prism is specific: it contains the true position 100% of the time, but also contains a position moved 50-150 km at random 98.4% of the time. At a median gap of 40 hours the prism covers hundreds of kilometres, so 'inside the prism' alone carries little information; the evidence has to come from radar cells that are rare in that water, which Test 1 does not find.
- A few prisms made GEOS reject the lens union: 27 retries with a different time step, 9 (gap, shift) pairs counted as no hit in Test 1, 5 cases skipped in Test 2. core/prism.py was out of scope; fix it there.
- Test 1 uses only the 1,338 gaps in the 15 busiest cells, the ones the region scan fetched radar for; the rest of the 5,624 have no radar loaded. Those cells are dominated by Brazilian offshore-oil traffic, so the result says little about open-ocean transits.
- Radar is not uniform in time (Sentinel-1 revisit patterns), so shifting by a few weeks can change coverage for reasons unrelated to ships; the shuffle averages over many shifts but cannot remove this.
- Unmatched radar cells include fixed structures (platforms), other ships GFW could not match, and waves; the filtered variant removes cells seen on 3 or more days but cannot remove the rest.
- No spatial shuffle was run (see Test 1).
- Test 2 is a physics check, not a radar recall: its 'truth' is another GFW event position, which is itself a point inside a longer event, and vessels that stop at events are not a random sample of dark ships.

## What would change the conclusion

- A real count whose bootstrap interval sits above 1 in the filtered variant on a larger set of gaps would move the claim from 'no evidence' to 'evidence'.
- Vessel-level attribution of matched radar detections would allow a true recall number.
- A land mask and radar fetched around shifted prisms would allow the spatial shuffle.

Data: Global Fishing Watch (CC BY-NC 4.0). Powered by Global Fishing Watch.

## Experiment 2: Brownian-bridge model (pre-registered 2026-11-21, before the run)

Hypothesis: the binary prism is too large to be specific (Test 2 above: it contains a position moved 50 to 150 km 98.4% of the time), but a Brownian-bridge movement model (Horne et al. 2007, Ecology 88:2354) is specific, so radar cells that score high under it beat chance.

Model. Ship seen at A (time t0) and B (t1), T hours apart. At time t with tau = (t - t0)/T the mean position is the geodesic interpolation from A to B and the variance per axis is sigma^2 * tau * (1 - tau) * T km^2 (sigma in km per square-root hour; variance floored at 1 km^2 at the ends). The density is truncated to the prism by giving -infinity to any point outside the exact lens at that time (the normalising constant of the truncation is ignored; it is a per-gap constant).

Steps, fixed in advance:
1. Fit sigma once by maximum likelihood on the synthetic recall cases of Test 2 only (known position of the ship at the time of its middle event; closed form sigma^2 = sum(d_i^2 / w_i) / (2n), where d is the distance of the true position from the bridge mean and w = tau(1 - tau)T). Radar is not used for fitting. The fit ignores truncation.
2. For each real gap of 48 hours or less in the 15 radar cells, score = the maximum over its unmatched radar cells of the bridge log-density, taking each cell's best value over the 30-minute sample times of its scene day at which the cell centre is inside the lens. Cells are tested by centre point against the exact lens (not the 0.01 degree box used in Test 1), so counts differ slightly from Test 1.
3. Null: the same score with each gap shifted by 7 to 21 days, K = 50 shuffles, same shift rule as Test 1. Reported: (a) the number of gaps with any scorable cell, real vs shuffled; (b) the mean score over scorable gaps, real vs the 50 shuffled means; (c) the number of real gaps whose score exceeds the 95th percentile of the pooled shuffled scores, against the count expected by chance (the mean over shuffles of the same count, about 5% of scorable gaps); (d) recall-side check: AUC of the true position's bridge log-density against 20 positions displaced 50 to 150 km at random per case (bootstrap interval over cases). Within one case the ranking depends only on distance from the bridge mean, so the AUC does not depend on sigma.
4. Verdict: signal if the interval of the count ratio in (c) excludes 1 or the p-value of (b) or (c) is below 0.05 and the AUC interval in (d) excludes 0.5; otherwise no signal. Both outcomes are reported as they come out; no parameter is changed after seeing results.
