.PHONY: check lint test web-build dev-api dev-web

check: lint test web-build

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
