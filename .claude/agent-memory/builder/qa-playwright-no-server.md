---
name: qa-playwright-no-server
description: How to screenshot and console-check a built web/dist without starting a server (Playwright route interception, Python, system python3)
metadata:
  type: reference
---

Verify a worktree's `web/dist` without starting a server (builder rule: never start a server).

- Use system `python3` (`/usr/bin/python3` or the user site package with playwright). `python3 -I` fails to find playwright; plain `python3` works.
- `page.route("http://127.0.0.1:8000/**", handler)`: serve static files from `web/dist` by path, fall back to `index.html`.
- `/api/*` GETs: `route.fetch()` then `route.fulfill(response=...)` to proxy the running app.
- `*/stream` (SSE) must be answered with `route.fulfill(status=204, body="")`. `route.fetch()` on an SSE stream never ends and crashes the run with TargetClosedError.
- Collect `console` error and `pageerror` events per context; 0 entries = zero console errors.
- Geometry checks: `getBoundingClientRect` for the chip vs the zoom controls, `scrollWidth` vs `clientWidth` for clipping.
- Hash routes: `#/watch`, `#/hormuz`; the Hormuz cluster card is `button.hz-card[data-card='cluster']`.
- Watch kind chips all start ON; clicking one turns it OFF. Plan the selector flow around that.

Related: [[opencode-worker-patterns]]

## Issue #254 run
- `route.fetch()` on `/api/edge/*` raises "Request context disposed" when the browser closes with requests pending. Wrap the handler in try/except and `route.abort()`; the errors are harmless. Use `route.fetch(timeout=15000)`.
- A top-bar hint set up once per visit vanishes when a screen rebuilds the bar. Keep the once-per-load state in a module variable, not in the per-call check.
- Hide the Patrol camera image with `style.visibility='hidden'` before a screenshot. The overlay still shows on the dark frame, and nobody on camera appears.
- Selectors: `.watch-row.is-dark`, `.watch-row.is-sanctioned_stop`, `.replay-play`, `.replay-clock`, `.guide-line.is-<screen>`, `.tour-hint`, `.patrol-frame-hint`.
