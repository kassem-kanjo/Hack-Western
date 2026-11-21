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
