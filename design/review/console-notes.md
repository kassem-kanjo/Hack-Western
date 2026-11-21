# Console review notes (issue #11)

Screens: `console-01.png` = `#/` (overview), `console-02.png` = `#/case/gap:ba74a0bbb00bf16c8bc319883f159b7a` (IRENES WISDOM, the case with the most radar detections in the candidate status), both 1440x900, Playwright + system Chrome.

**01 Overview.** Matches: 48 px top bar, KPI strip on top of the map, 440 px ranked rail, dashed amber gap lines, legend and attribution bottom. Differs: the wireframe KPI row has 5 tiles (ships tracked, dark events, hours dark, sightings, meetings); the build shows the 4 the brief asked for (gaps scanned 5,624, radar nearby 109, ruled out 90, unexplained 19), because the API has no ships-tracked, hours or meetings totals in the snapshot. Why: the numbers are only what the snapshot provides; nothing is invented.

**01 Overview map.** Matches: dashed gap lines, amber for candidates, filled start and hollow end. Differs: no teal AIS dots and no radar diamonds on the overview, because the API carries only gap endpoints per case. Why: no per-ship AIS positions in the frozen schema.

**02 Case file.** Matches: footprint dotted outline, violet lens at the selected time, white diamonds inside the lens, timeline with radar pass markers and the readout, right rail with sources and caveats. Differs: the score hero shows a dash and "Not scored" (score and band are null for every case in the snapshot) and there are no score factors, so the "Why it was flagged" list shows the grouped radar reasons instead. Why: the snapshot is unscored.

**02 Case file, timeline.** Matches: scrubber, play, +4 h ticks. Differs: the scrubber opens at the first radar pass (not t = 0) so the lens and a live detection show at once; the "Reappears" label overlaps the gap line near the endpoint (not fixed). Why: a point at t = 0 says nothing.

**Known gaps.** Right-rail overflow at 1440 px is fixed (rail scrollWidth = clientWidth). Tick label "+80 h" wraps. The "radar pass 1" label sits under the first marker and can be hidden at the scrubber's left edge.
