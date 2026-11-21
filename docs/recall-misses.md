# Why the silence rule misses in-scope injected gaps

Generated: 2026-11-21T15:36:21+00:00
Seed: 7
Source: aisstream files in ais/BALTIC_WATCH

An in-scope gap is one whose start ping reports at least 2 knots, which is the only case the silence rule claims. Each missed gap is counted under the first reason that explains it, checked in the order of the table.

In-scope gaps: 92; found: 55; missed: 37.

| Reason | Count | Example MMSI and gap (UTC) |
| --- | --- | --- |
| Gap no longer than min_gap_min | 0 | - |
| Receiver coverage below floor | 37 | `210549000`, 2026-11-21 04:31 UTC to 2026-11-21 06:08 UTC |
| An event covers <50% of the gap | 0 | - |
| Detector's last ping differs | 0 | - |
| Other | 0 | - |

## Reading

Of 92 in-scope injected gaps, 55 were found and 37 were missed, and receiver coverage below floor accounts for 37 of the misses. A coverage miss means at least one minute of the gap fell below the receiver's message floor, which the rule reads as a hole in our own listening rather than a dark ship. Coverage and duration misses are the rule working as designed, while a partial-event, last-ping or other miss means the harness and the detector disagree and is worth fixing.

Data: our own AIS recorder. No Global Fishing Watch data is used in this report.
