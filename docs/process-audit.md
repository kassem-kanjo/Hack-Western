# Process audit (every 30 min)

Format: went well · went wrong · DeepSeek vs Claude code share · rule breaks · one change for the next 30 min.

## Fri 20 Nov, 23:40 ET (audit 1, covers 21:30-23:40)
- **Went well:** feasibility gates killed two wrong plans early (Baltic-only, GFW-gaps-only) with real calls; Opus Auditor caught that our opener failed our own physics before we built on it; worker claims were re-verified (found a critical maplibre CVE and CI red since #1).
- **Went wrong:** design was briefed before the data probe (one redo); CI was red for 4 pushes and nobody looked (fixed: health loop); two leads shared one checkout (fixed: worktree per lane); I guessed timestamps instead of running `date` (docs said "1:15 AM" at 23:40).
- **Code share:** DeepSeek/GLM coders: scaffold, store/ingest pieces, edge protocol/detector, #28 speeds+filters. Claude-written: core prisms (Sonnet lead skipped coders), region scan, edge safety core/supervisor, recorder (Chief, time-critical). Estimate ~40% DeepSeek, too low.
- **Rule breaks:** Chief wrote the recorder (justified: live data loss); Sonnet lead skipped OpenCode; a builder handed back while its own coders were still running.
- **Change for next 30 min:** every timestamp comes from `date`; every new brief names DeepSeek pieces and says "don't hand back while coders run"; small issues go lead-less straight to DeepSeek.
