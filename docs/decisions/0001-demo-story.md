# Decision 0001: the demo story (for Kassem, CP1 9 AM)

Status: **proposed** by Chief (Sat 21 Nov, 00:28 ET); decided by Kassem at CP1.

## Evidence (all committed)
- Funnel: 5,624 gaps → 109 with unmatched radar in the prism → 19 unexplained after physics checks (docs/rerank.md).
- Test 1 (null shuffle): radar-in-prism does not beat chance: ratio 1.07, 95% CI 0.91-1.25 (docs/validation.md).
- Test 2 (Brownian bridge, pre-registered): localisation is real (AUC 0.87, 0.84-0.90), radar signal is not (ratio 1.03).
- Held-out test (open-ocean cells, May-Aug data, pre-registered): **inconclusive, leans null**: 4 real vs 5.7 shifted
  (ratio 0.70, CI 0.15-1.37). Option B is not supported. **Chief recommends A.** Radar experiments stopped.

## Options
**A. "Evidence before accusation" (recommended if the held-out test is null).** Dark Fleet shows where a dark
ship most likely was (bridge heat map, validated AUC 0.87), checks every satellite detection against the
satellite's real pass time, clears false alarms with a stated reason (90 of 109), and says plainly when the
remaining evidence is no better than chance. Pitch: "we built a dark-ship detector, tested it like scientists,
and found most 'radar caught it' stories are background traffic. Here's the tool that tells you which are."
Strengths: honest, rigorous, contrarian, memorable to engineering judges; every number reproducible.
Risk: no "we caught a smuggler" moment.

**B. "Radar catches dark ships in open ocean" (only if the held-out test shows signal, CI > 1).** Same product,
headline is the held-out effect size outside oil-field areas. Strength: a positive, validated finding.
Risk: smaller n; must be phrased as exactly what the test showed.

Either way the build is identical (case list, case file with bridge heat map and scene-time radar check,
validation panel); only the words and the opening case change. Build continues tonight on that shared core.
