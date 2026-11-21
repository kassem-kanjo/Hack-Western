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
