# Plan

Issues on GitHub are the source of truth; this is the map. Makers: B = Builder (OpenCode DeepSeek flash
coders + flash reviewer), S = Sonnet lead (hard or failed-twice pieces), D = Designer, Sc = Scout,
C = Chief (decisions, review, commits). Opus Auditor reviews at each checkpoint.

## M0 Foundation (Fri night → CP1, Sat 9 AM)
| Issue | Work | Maker | Depends | Gate |
|---|---|---|---|---|
| Scaffold | uv project, ruff, pytest, Makefile, web Vite skeleton, CI | B | - | `make check` exit 0 |
| Core: prism | lens geometry on the sphere + property tests | S (algorithm) | Scaffold | tests green, properties in architecture.md |
| Core: matching | SAR-in-lens + dark rendezvous search + tests | S | prism | tests green |
| GFW client + probe | typed client, disk cache, probe script answering F1-F3 | B | Scaffold | runs on doc fixtures; real run when token exists |
| aisstream collector | WebSocket client, region filter, silence detector (F5) | B | Scaffold | unit tests on recorded messages |
| Store | repository interface; SQLite impl; Timescale schema + continuous aggregate (F6) | B | Scaffold | tests on SQLite |
| Edge | detector (YOLOX-nano ONNX), safety core, supervisor, IPC (F7) | B + S review | Scaffold | kill-detector test: core stays up, restart logged |
| Design | tokens + wireframes of every console screen/state | D → Critic → Director | mission | PNGs approved by Chief |
| Research | prior art, real Baltic cases, sanctions data licence, judge angle | Sc | - | sourced notes |

## M1 End to end (CP2, Sat ~3 PM)
API (cases, vessels, stats, snapshot mode) · console (map, gaps, lenses, case file, timeline) ·
risk score + sanctions join · real-data run on the chosen region.

## M2 Demo ready (CP3, Sat ~10 PM)
Live layer · edge → API sync and console view · polish against wireframes · deploy (Vultr + domain) ·
demo script rehearsed to 3:00 · Entire sessions/graph/Trails in use.

## M3 Submit (Sun 6-8:30 AM)
Freeze · Devpost text + 60 s video · prize tracks ticked on Devpost **and** live site (must match) ·
submit by 8:30 AM. After: DEV post.
