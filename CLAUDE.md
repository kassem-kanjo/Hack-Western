# Dark Fleet (Hack Western 13)

Finds ships that switched off their transponders, shows where they could have been while dark and who
they could have met. Hackathon build; hacking ends **Sun 22 Nov 2026, 9:00 AM ET**.

Start here: `docs/STATUS.md` (live state) → `docs/mission.md` (why, prizes, feasibility gates) →
`docs/architecture.md` (how) → `docs/plan.md` (work breakdown).

## Rules for every agent
- One writer per file. Touch only the files your brief lists.
- Keys live in `.env` only (gitignored). Never print, log, commit or send them to any model. The browser
  never calls third-party APIs; `api/` does.
- `core/` is pure (no I/O). Every geometry function has property tests.
- Every number shown in the UI or the pitch comes from a real run; attribute "Powered by Global Fishing
  Watch" (link) on every view that uses it.
- Python 3.12 via `uv` (`~/.local/bin/uv`); run tests with `uv run pytest`. Web: `npm` in `web/`.
- Commits reference an issue: `feat(core): prism lens (#3)`. Chief commits and pushes; workers don't.
