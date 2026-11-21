# Demo footage (issue #18)

1. Install the package: `cd media && npm install` (Chrome is used when installed; otherwise run `npx playwright install chromium` once).
2. Start the console and its API on a local port; the default base is `http://localhost:8013/`.
3. Record: `node record.mjs --base http://localhost:8013/ --out out/demo-v1`.
4. It opens the console with `?nointro#/`, presses **P**, holds each of the eight beats (durations at the top of `record.mjs`), takes the step-4 poster, and presses **Escape**.
5. It writes `out/demo-v1.mp4` (1920x1080 H.264), `out/demo-v1-square.mp4` (1080x1080 centre crop) and `out/demo-v1-poster.png`, then prints each path and duration.
6. The raw WebM is temporary; `out/` and `node_modules/` are gitignored.
