# Hack Western 13: context (gathered 2026-11-20, ~10:30 PM)

Sources: hack-western-13.devpost.com (prizes, rules), mlh.com/events/hack-western-13/prizes, hackwestern.com/live (schedule, scavenger hunt, sponsors, mentors).

## Key times
- Hacking: Fri Nov 20, 9:00 PM → **Sun Nov 22, 9:00 AM** (Devpost deadline, no edits after).
- Sponsor track judging: Sun 10:00 AM. Main judging (demo fair): Sun 11:30 AM–1 PM. Closing: 2 PM.
- General judging: expo style, 3 judges, **3 min demo + 1 min Q&A each, no slide decks**. Show what you built.

## Rules that matter for prize stacking
- One project per team, up to 4 members, all code written during the hack (ideas in advance are fine).
- **Sponsor tracks: pick them on BOTH the live site and Devpost; they must match.** Sponsors shortlist teams for separate judging sessions. Sponsor scores don't affect overall awards.
- So one project can enter overall + Most Adventurous + every sponsor/MLH track it genuinely fits.

## Prizes (16 on Devpost + 1 MLH extra)
| Prize | Award | What it asks |
|---|---|---|
| 1st / 2nd / 3rd overall | iPad 11" / AirPods Pro 3 / Fitbit Air each | General judging |
| **Organizers' Choice: Most Adventurous** | **Arc'teryx Bird Head toque** each | "A hack that enables others to discover the unknown." |
| Best Health Hack (Sun Life) | $100 Amazon each | Creative technical fix to a real health problem; pitch who it helps and the impact |
| Best Use of QNX | $1,000 + 2× $250 | Embedded system on QNX OS using ≥1 QNX open-source AI module; real-time/reliability use case; demo on hardware. Loaner Raspberry Pi 5s with QNX |
| Canada Life Future Me | $100 Visa each | Help Gen Z act on long-term financial future (motivation, visualisation, nudges, education) |
| TinyFish: Hire Your First AI Employee | $75 gift card + $100 credits each + 1:1 with eng lead | Browser-only job done end to end on the live web with TinyFish agents. **Must post demo on X or LinkedIn tagging TinyFish.** Credits: tinyfish.ai/students |
| Best Hardware Prototype (PCBWay) | US$50 PCB credit (team) | Hardware prototype for a real problem; show next fab version |
| MLH Best Use of Gemini API | MLH swag kits | Use Gemini API |
| MLH Best Use of Entire | Mini portable projector | Git forge for agent-human work: Sessions, Graph, Brain, Trails; show token efficiency |
| MLH Best Use of Solana | Hardware wallets | High-frequency/cheap transactions |
| MLH Best Use of Tiger Data | Stream Deck Mini | Postgres + time-series, continuous aggregates, real-time dashboards |
| MLH Best Use of Vultr | Assorted | Deploy on Vultr cloud / GPUs (free credits) |
| MLH Best Use of Auth0 | Wireless headphones | Use any Auth0 API (login, MFA, Auth0 for AI Agents) |
| MLH Best Domain (GoDaddy Registry) | Digital gift card | Register a domain via GoDaddy Registry (claim code on MLH page) |
| MLH DEV Writing Challenge (MLH page only) | $100 | Publish the project story on DEV |

## Sponsors present
Scotiabank, Canada Life, QNX, Sun Life, Manulife, StarTech.com, CSE, Commure, TinyFish, Celsius, Autodesk, Accenture, Shopify, uBit, OpenAI, PCBWay, Summerfresh, DoorDash.

## Useful workshops (Sat)
- 1:30 PM OpenAI: "How to make your product not look vibe coded" (Rm 2230)
- 4:30 PM QNX intro (2235) · 6:30 PM TinyFish 101 (2235) · 7:00 PM MLH (2230)
- Mentors on Slack incl. Rafay Kashif (C++, Linux & QNX, Rust) and Lukas Bozinov (AI agents, hardware testing).

## Scavenger hunt (points → swag at help desk)
Workshop 20 pts, sponsor workshop 25, activity 10, win 50. Bonus tasks worth 218 pts (golden egg 75, rock wall 20, photobooth 15…). Store includes an **HW beanie for 75 pts** (separate from the Most Adventurous Arc'teryx toque).

## QNX challenge (from the sponsor slide at opening ceremony)
- Hard requirements: (1) runs on QNX OS; (2) uses one of the open-source AI modules listed at **oss.qnx.com**.
- Judging: a "cannot-fail" embedded application? Needs real-time or reliability? AI used in an interesting or creative way? Running on the device, **not in the cloud**?
- Loaner Raspberry Pi 5s preloaded with QNX. The QNX quick-start image ships Python 3 + SSH (qnxuser@qnxpi).
- AI packages on oss.qnx.com that install directly (apk, aarch64), checked 2026-11-20:
  - `python3-onnxruntime` 1.24.4 / `onnxruntime` (QNX 8.0.5): run any ONNX model (YOLO, classifiers) from Python. **Easiest path.**
  - `libopencv_*` 4.12 incl. `dnn`, `face`, `aruco`, `calib3d` (QNX 8.0.3)
  - `whisper.cpp` (8.0.5): on-device speech to text
  - `llama.cpp` / `llama-server` (8.0.3): small on-device LLM
  - TensorFlow is cross-compile only (no apk), so avoid it.
- Open checks at the booth: QNX version on the loaner image (packages target 8.0.3/8.0.5), whether `apk` can reach the repo from the Pi, camera/sensor loans.

## State
- GitHub repo `kassem-kanjo/Hack-Western`: **public, empty**. Local folder empty apart from this file.
- Devpost: registered, **no project created yet**.
