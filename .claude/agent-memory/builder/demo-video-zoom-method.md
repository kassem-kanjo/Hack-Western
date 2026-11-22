---
name: demo-video-zoom-method
description: Systematic method for Remotion demo-video zooms on recorded app footage (find stable states, track the cursor, measure boxes, check view edges); avoids the "zoomed on the wrong thing" failure
metadata:
  type: feedback
---

Zoom targets must come from frames of the footage that is actually on screen, never from an older timeline.
**Why:** v5 of the Dark Fleet demo zoomed into empty map and scrolled-away panels because targets came from
a stale timeline; Kassem was angry about bad zooms (2026-11-22).
**How to apply (worked for v6B):**
- Find stable footage states with a per-frame mean-abs-diff at 10 fps (spikes = page jumps/scrolls); keep
  every segment inside one state, crossfade only at state changes while the camera is WIDE and still.
- Static UI states can be played at any rate (0.4x-1.6x) to fit the voice; nobody sees it.
- Track the recorded cursor by template matching (cv2.matchTemplate on a crop of the cursor dot) so the
  validator can refuse zooms while it moves. Mask false positives (e.g. a LIVE dot).
- Measure boxes on full-res frames, then also check the *view edges*: dense UIs make a 1.8x view slice text
  at its edges; add a `focus` centre so edges fall on panel borders/row gaps. Crop the simulated view per
  zoom and look before rendering.
- The outgoing clip under a crossfade must not run past a page jump: hold its last frame instead.
- Recorded footage may simply lack an element the script names (e.g. "Alarm in N ms"); report it, don't fake it.
Related: [[qa-playwright-no-server]]
