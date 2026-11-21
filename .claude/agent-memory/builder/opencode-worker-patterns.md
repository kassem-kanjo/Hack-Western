---
name: opencode-worker-patterns
description: What worked and failed when delegating scaffold work to OpenCode flash workers via delegate.sh (Dark Fleet, 2026-11-20)
metadata:
  type: feedback
---

Delegating scaffold work to `opencode-go/deepseek-v4.1-flash` (Python/config) and `opencode-go/glm-5.3-flash` (web/npm) via `delegate.sh` worked: both finished in 30-80 s, in-scope diffs, no quota hit.

**Why:** first real run of the Builder loop on the Dark Fleet scaffold (issue #1).

**How to apply:**
- Worker shells do not have `uv` on PATH. Tell the brief to use `~/.local/bin/uv` or put it on PATH in the `--check` command, or the check exits 127 even when the work is right.
- Give `--check` commands that do not depend on other workers' files (the web build was absent in the Python worktree).
- `git apply --check` then `git apply` the `diff.patch` from `<out>/<name>/` into the real repo; it picked up new files and lockfiles fine.
- Run the repo gate yourself after applying (`make check`), not the worker's check alone.
- Worker-created `node_modules`/`.venv` are gitignored; run `npm ci` / `uv sync` in the real repo for the gate.
- `~/.config/opencode/opencode.json` was absent on this machine; the workers ran anyway through the skill's `config/opencode.json`.

## Second run: GFW client + storage layer (issues #4, #6, 2026-11-20)
- Two `deepseek-v4.1-flash` coders in parallel on disjoint files: 197 s and 369 s, both check exit 0, in-scope diffs. Both applied cleanly.
- **Worktree base must include uncommitted work.** `delegate.sh --base` only sees commits. To share a not-yet-committed contract file (e.g. dataclasses) with two coders, snapshot the working tree to a commit object without moving any ref: `GIT_INDEX_FILE=<tmp> git read-tree HEAD; git add -A; git write-tree; git commit-tree <tree> -p HEAD -m "Refs #N ..."`. The commit-guard hook needs a `Refs #N` in that message. Never push it.
- **Coder output was good but had two real gaps.** The store coder wrote `psycopg` `connection.executemany` (does not exist in psycopg 3.3; use a cursor). The coder's Timescale test was skipped (no live DB), so a `search_path=<schema>` bug (dropping `public`, where `create_hypertable` lives) only showed on the first live run. Rule: any DB code gets one live test run before the integration step.
- Briefs that name the exact check command and the venv path with `PYTHONPATH=src` worked. Check-command quoting with a space in the repo path needed quotes inside the `--check` string.
- The worker's full-suite check passed in its own worktree while the real repo had another builder's failing edge test. Run the repo gate yourself on the merged tree.
- Ruff `E501` on my own files came from the coder's long lines; `ruff format` fixed them. Always run `ruff format` then `ruff check` on the merged files.
- Hard rule kept: one live API call for the integration run; the disk cache means any rerun replays without new API calls.
