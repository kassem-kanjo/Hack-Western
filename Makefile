.PHONY: check lint test web-build dev-api dev-web demo-check snapshot sunday watch app

# The demo gate must run from make's /bin/sh, which may not have uv on PATH.
UV ?= $(HOME)/.local/bin/uv

check: lint test web-build

snapshot:
	uv run python scripts/build_all.py

sunday:
	bash scripts/sunday.sh

# V3 watch screen (#86): rebuild data/snapshot/watch.json every 5 minutes.
watch:
	uv run python scripts/build_watch.py --loop 300

# Native desktop shell (#86): build "Dark Fleet.app" on the Desktop.
app:
	bash scripts/make_mac_app.sh

lint:
	uv run ruff check . && uv run ruff format --check .

test:
	uv run pytest

web-build:
	cd web && npm run build

dev-api:
	uv run uvicorn darkfleet.api.app:app --reload --port 8000

dev-web:
	cd web && npm run dev

health:
	bash scripts/health.sh

# Pre-demo gate: serve the real build over the Sunday snapshot and walk every
# presentation beat in Chrome. Never leaves a server behind, even when a check fails.
demo-check: web-build
	@mkdir -p out/demo-check
	@rm -f out/demo-check/step-*.png
	@set -e; \
	DARKFLEET_WEB_DIST=web/dist DARKFLEET_DATA=data/snapshot \
		$(UV) run uvicorn darkfleet.api.app:app --port 8099 >out/demo-check/server.log 2>&1 & \
	pid=$$!; \
	trap 'kill $$pid 2>/dev/null || true' EXIT INT TERM; \
	for i in $$(seq 1 30); do \
		if curl -fsS http://127.0.0.1:8099/api/health >/dev/null 2>&1; then break; fi; \
		if [ $$i -eq 30 ]; then \
			echo "demo-check: API not healthy after 30 s (see out/demo-check/server.log)" >&2; \
			exit 1; \
		fi; \
		sleep 1; \
	done; \
	$(UV) run --no-project --with playwright python scripts/demo_check.py \
		--base http://127.0.0.1:8099 --out out/demo-check
