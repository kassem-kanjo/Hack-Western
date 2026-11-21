# Region scan (gate F4)

Real Global Fishing Watch data, 15 Aug - 15 Nov 2026 (full global pagination, 86,438 gap events). Filter: vessel type not `gear`, gap 6-96 h: **5,624 gaps** kept. Gap midpoints binned into 5x5 degree cells, top 15 by count. Unmatched SAR (`public-global-sar-presence`, ~1 km daily cells) fetched per cell (bbox padded 1 degree, gap date span), then `core.match.candidate_sightings` with `max_speed_knots(type)` per gap (cheap footprint prefilter first). Script: `scripts/scan_regions.py`; live API requests: 472 (cached in `data/cache/scan/`).

**Result: 5 of 15 cells have no SAR cells at all in the span; 10 have candidates. 109 gaps have at least one candidate.** Cells in the open Pacific and Indian Ocean are mostly fishing/inactive vessels with no radar coverage.

| Cell (lat, lon) | Gaps | Gaps with >=1 SAR candidate | Total candidates | SAR cells fetched | Top vessel types |
|---|---|---|---|---|---|
| 40..45N, 170..175E | 168 | 0 | 0 | 0 | fishing 165, carrier 1, other_non_fishing 1 |
| 0..5N, 165..170E | 136 | 0 | 0 | 0 | inactive 130, fishing 5, carrier 1 |
| -30..-25N, -45..-40E | 127 | 34 | 241 | 239 | other_non_fishing 112, fishing 8, inactive 7 |
| -20..-15N, -80..-75E | 105 | 14 | 80 | 196 | fishing 104, insufficient_data 1 |
| -25..-20N, -45..-40E | 102 | 34 | 800 | 975 | other_non_fishing 98, cargo 2, fishing 2 |
| 5..10N, -50..-45E | 101 | 18 | 33 | 86 | insufficient_data 87, inactive 11, fishing 2 |
| 10..15N, -170..-165E | 98 | 0 | 0 | 0 | inactive 70, insufficient_data 26, fishing 2 |
| 40..45N, 155..160E | 86 | 0 | 0 | 0 | fishing 85, insufficient_data 1 |
| 40..45N, 175..180E | 77 | 0 | 0 | 0 | fishing 75, other_non_fishing 1, inactive 1 |
| -35..-30N, 95..100E | 66 | 0 | 0 | 0 | fishing 33, inactive 30, carrier 2 |
| -35..-30N, 100..105E | 62 | 0 | 0 | 0 | insufficient_data 28, fishing 16, inactive 14 |
| 40..45N, 150..155E | 56 | 8 | 135 | 109 | fishing 53, other_non_fishing 2, cargo 1 |
| -20..-15N, 170..175E | 55 | 1 | 3 | 5 | inactive 46, fishing 8, other_non_fishing 1 |
| 10..15N, -50..-45E | 50 | 0 | 0 | 0 | inactive 39, insufficient_data 10, fishing 1 |
| -20..-15N, -130..-125E | 49 | 0 | 0 | 0 | inactive 49 |

Bbox of each cell is the cell itself (SAR fetched 1 degree wider).

## Best cases
Full top-20 with candidate cells in `data/cases/best_cases.json` (gitignored). Ranked: non-fishing, intentional-disabling flag, 2-4 candidates, longer gap.

1. **EAGLE CAMPOS** (MYS, other_non_fishing), gap 2026-10-02 18:34 to 10-03 17:13 UTC, 22.7 h, off (-25.326, -42.693) on (-25.325, -42.694), 3 candidates (best slack 0.28 at -24.97, -43.42 on 10-02). Offshore Santos Basin; the vessel came back to where it left, so it was loitering and the prism is a full circle-like lens.
2. **LISBOA** (MLT, other_non_fishing), 2026-10-19 15:40 to 10-20 12:56, 21.3 h, off (-25.326, -42.690) on (-25.322, -42.692), 3 candidates. Same area and time as EAGLE CANOAS: possible meeting.
3. **EAGLE CANOAS** (SGP, other_non_fishing), 2026-10-19 16:10 to 10-20 10:23, 18.2 h, off (-25.487, -42.778) on (-25.487, -42.780), 3 candidates. Dark within 20 km of LISBOA at the same hours.
4. **CL YICHUN** (MHL, cargo), 2026-09-11 18:02 to 09-12 16:56, 22.9 h, off (7.988, -51.666) on (5.609, -48.279), 2 candidates (4.03, -50.83) and (4.04, -50.98) on 09-12. A moving cargo ship, off Guyana/Suriname; the most clearly 'went dark while transiting' story.
5. **210700028** (CYP, insufficient_data), 2026-10-23 01:09 to 10-26 12:33, 83.4 h, off (4.901, -45.024) on (9.179, -52.143), 4 candidates (best slack 0.91 near 5.32, -50.98 on 10-24). Highest slack, but an unnamed MMSI.

## Caveats
- Brazil cells (-30..-20N, -45..-40E): 112 of 127 and 98 of 102 gaps are `other_non_fishing`, almost all near-stationary vessels (off and on within ~1 km) around offshore oil fields. Candidates there are 50-100 km from the vessel, reachable only because 17-22 h at ~19 kn is a large radius; these are weak cases and some SAR cells may be platforms/FPSOs (fixed structures) rather than ships.
- 5..10N, -50..-45E: many `210700xxx` CYP MMSIs of type `insufficient_data` with consecutive numbers; likely a fleet of small or tag-like transmitters, not verified.
- SAR has a date, not a time; a candidate means the vessel could have been in that ~1 km cell at some moment of that UTC day. No AIS cross-check was applied (public per-vessel presence is down), so a candidate may be another AIS-visible ship's unmatched blob.
- Candidate counts are per gap; total candidates in the 800-candidate cell (-25..-20N, -45..-40E) are dominated by the large radius of long gaps near dense offshore-supply traffic.
- Hormuz was not in the top 15 by gap count (55 gaps Aug-Nov); see gate F2 for its SAR coverage.
