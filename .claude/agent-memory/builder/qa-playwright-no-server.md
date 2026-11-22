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
