.PHONY: check lint test web-build dev-api dev-web snapshot

check: lint test web-build

snapshot:
	uv run python scripts/build_all.py

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
