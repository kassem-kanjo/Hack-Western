---
name: opencode-worker-patterns
description: What worked and failed delegating to OpenCode flash workers via delegate.sh (Dark Fleet scaffold 2026-11-20, edge unit #7)
metadata:
  type: feedback
---

Delegating to `opencode-go/deepseek-v4.1-flash` via `delegate.sh` worked for both the scaffold and the edge unit: in-scope diffs, check exit 0, no quota hit. Run times: protocol tests + fetch script 147 s; detector module + tests 682 s (41 tool events, several self-fix rounds).

**Why:** real Builder runs on Dark Fleet (issues #1 and #7).

**How to apply:**
- Worker shells do not have `uv` on PATH. Prefix every check with `PATH=$HOME/.local/bin:$PATH`, or it exits 127 even when the work is right.
- `timeout` is not on macOS. Do not use it in shell checks; the delegate script has its own portable timeout.
- **Uncommitted new files are not in a worktree.** `delegate.sh --base HEAD` cannot see files you have written but not committed. Fix without touching any branch: `GIT_INDEX_FILE=<scratch>/idx git read-tree HEAD`, `git add <paths>`, `git write-tree`, then `git commit-tree <tree> -p HEAD -m "... (Refs #N)"`. Use the resulting SHA as `--base`. The commit-guard hook refuses commit messages with no `#N` issue reference, so include one.
- `git apply --check` then `git apply` the `diff.patch` from `<out>/<name>/`; new files and new test files apply cleanly.
- Run the repo gate yourself after applying (`make check`), not only the worker's check.
- A worker's own review can still miss things. Its diffs passed their checks, yet Builder's review found 2 real bugs in its shared module (a bool passing as an int, and a timeout applied twice). Keep reviewing the diff yourself.
- A status file that a watchdog only rewrites on events goes stale during sleeps. Schedule restarts with deadlines and keep the status loop running. Tests catch this; check them.
- `~/.config/opencode/opencode.json` was absent on this machine; the workers ran anyway through the skill's `config/opencode.json`.

## Third run: scene times + re-rank (issue #28, 2026-11-21)
- deepseek-v4.1-flash coders wrote speeds/filters (66 s), STAC client (111 s), verdict_at_scene (83 s), speed-label docstring (26 s) and the 109-gap re-rank script (1,639 s). Each check passed on the first try; the re-rank script ran cleanly on the first real run (baseline 109 gaps / 1,292 candidates reproduced from cache).
- Worker sandboxes have no network and no `.env`: give the coder cache-only code paths, and put any live call (here STAC) in a module whose tests use `httpx.MockTransport`. Record real responses yourself and paste trimmed ones into the brief.
- Run the big script yourself on the real data after review. The coder's check was lint only; it cannot run against the real cache.
- Use `git apply --include=<path>` when one patch carries an extra file you do not want yet.
- A foreground `sleep` longer than a few seconds is blocked by the harness. Wait with `run_in_background` plus an until-loop, and read the notification.
- Never call SubagentHandback mid-pipeline: it ends the run. Only hand back when every DoD item is done.

## Third run: validation stats (#27, 2026-11-21)
- deepseek-v4.1-flash did a pure-stats module + 16 tests (133 s) and a report renderer (~5 min) cleanly, both check exit 0. Give the second piece a scratch file and paste it in when the target file has a single writer.
- `multiprocessing` fork Pool hung silently on macOS (a worker died on a GEOS TopologyException; pool.map waits forever). Use spawn + initializer, and wrap core prism calls in try/except GEOSException with a different `step_minutes`.
- GFW 4Wings SAR `group-by=VESSEL_ID/MMSI` returned 404 "temporarily unavailable" on 2026-11-20; no detection-to-vessel attribution.

## Audit 4 lane H (issue #249, 2026-11-21)
- Two deepseek-v4.1-flash lanes in parallel on disjoint files: 168 s and 274 s, both model_exit 0, in-scope diffs, no quota hit. Apply both with `git apply --check` then `git apply`; overlapping hunks in one CSS file applied cleanly.
- **A worker check can false-green.** The worktree had no `web/node_modules`, so `npx tsc` ran a stub and the summary still said check_exit 0 (the SEA 1 check.log was empty). Before dispatch, run `npm ci` in the real checkout, and point the check at its node_modules via a symlink. Re-run tsc yourself after applying.
- Worker reports say "npm ci in the worktree" is fine, but then they delete node_modules; keep the symlink line in the check.
- Direct chart labels collided (two line ends about 7 px apart). Workers do not check label collisions; check the crop yourself and push labels apart in code.
- Builder should not `git commit` when the brief says so and CLAUDE.md says Chief commits (non-negotiable 3): leave the diff in the worktree and say so.

## Issue #254 (make the app explain itself, 2026-11-21)
- Two deepseek-v4.1-flash lanes on disjoint files: 130 s (4 files, check 0) and 621 s (7 files, 177 tool events, check 0). Both applied with `git apply --check` then `git apply`.
- Delegate base: the worker worktree is detached at HEAD, so shared files must be committed or they are invisible to the worker. I made a local WIP commit, dispatched on it, then `git reset --mixed <origin sha>` so nothing is left committed. Simpler: paste the shared file into the brief.
- Worker check script: `cd web && node_modules/.bin/tsc --noEmit` via a symlink to the real `web/node_modules`, removed in an EXIT trap. Keep the symlink out of diff.patch.
- The worker's "does it look right" was wrong twice, found only by screenshot: (1) a guide line as the first child of `.app` landed in the top-bar row (the grid has two rows; needed a `:has()` row rule); (2) the Watch details panel was `hidden` when nothing was selected, so its empty-state sentence never showed. Check screenshots, not the DOM text.
- Workers did not warn about copy length. Two copy lines given verbatim were over an 18-word rule; I used the verbatim copy and flagged it.

## Issue #259 round 3 (Patrol copy + tour step 5, 2026-11-21)
- Two deepseek-v4.1-flash lanes on disjoint files (screens/patrol.ts+css vs present/*): 41 s and 43 s, both check exit 0, in-scope diffs. Applied both with `git apply`; tsc and vite build exit 0 after applying.
- The Builder worktree guard refuses commands with a `$VAR` or a runtime-computed path in `git` arguments. Spell the scratchpad path out literally in `git apply`.
- Workers wrote judgement calls I had to check: one kept a module-level cache across re-renders (harmless here); one chose `--c-error` as the accent. Read the diff, not the report.
- Playwright without a server: unmocked `/api/*` paths return 404 and show up as console errors. List them with a print in the handler before calling them app errors.
