#!/usr/bin/env bash
# Health loop: re-verify everything that can silently break. Exit 1 if anything is red.
# Run at every step and on a timer: `bash scripts/health.sh`.
set -u
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
GH="${GH_BIN:-$(grep -s '^GH_BIN=' ~/.claude/machine.env | cut -d= -f2)}"; GH="${GH:-gh}"
REPO=kassem-kanjo/Hack-Western
red=0; say(){ printf '%-10s %s\n' "$1" "$2"; }

# 1. Recorder alive and writing (file touched in the last 10 min)
if pgrep -f record_ais.py >/dev/null; then
  f=$(ls -t data/cache/ais/BALTIC_WATCH/*.jsonl.gz 2>/dev/null | head -1)
  if [ -n "$f" ] && [ -n "$(find "$f" -mmin -10 2>/dev/null)" ]; then say OK "recorder alive, latest $(basename "$f") $(du -h "$f" | cut -f1)"
  else say RED "recorder running but no data written in 10 min"; red=1; fi
else say RED "recorder NOT running (restart: see docs/STATUS.md)"; red=1; fi

# 2. CI on main
ci=$("$GH" run list -R $REPO -b main -L 1 --json conclusion,status,displayTitle -q '.[0] | "\(.status) \(.conclusion) \(.displayTitle)"' 2>/dev/null)
case "$ci" in completed\ success*) say OK "CI main: $ci";; in_progress*|queued*) say WAIT "CI main: $ci";; *) say RED "CI main: $ci"; red=1;; esac

# 3. Open PRs and their checks
"$GH" pr list -R $REPO --json number,title,statusCheckRollup -q '.[] | "PR #\(.number) \(.title) [\([.statusCheckRollup[]?.conclusion] | join(","))]"' 2>/dev/null | sed 's/^/INFO       /'

# 4. Gate on a clean copy of origin/main (work in progress in the main checkout is ignored)
git fetch -q origin main
W=.worktrees/health
[ -d "$W" ] || git worktree add -q --detach "$W" origin/main
git -C "$W" checkout -q --detach origin/main
if [ ! -d "$W/web/node_modules" ] || ! cmp -s "$W/web/package-lock.json" "$W/web/node_modules/.lock-copy"; then
  (npm ci --prefix "$W/web" >/dev/null 2>&1 && cp "$W/web/package-lock.json" "$W/web/node_modules/.lock-copy")
fi
if make -C "$W" check >/tmp/health-make.log 2>&1; then say OK "make check on origin/main $(git -C "$W" rev-parse --short HEAD)"
else say RED "make check on origin/main failed (tail /tmp/health-make.log)"; red=1; fi
# Work in progress (builders) in the main checkout: report only
wip=$(git status --porcelain | wc -l | tr -d ' '); [ "$wip" != 0 ] && say INFO "$wip uncommitted path(s) in checkout (builders' WIP)"

exit $red
