.PHONY: setup format lint typecheck test check run hooks translations docker-up docker-down docker-logs image-check backup-check

setup:
	uv sync --all-groups
	@test -f .env || cp .env.example .env
	uv run pre-commit install

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

run:
	uv run uvicorn --factory mintflow.main:create_app --reload --no-access-log --no-proxy-headers

hooks:
	uv run pre-commit run --all-files

# Refresh the Web catalogues after changing user-facing strings; lists what still needs translating.
translations:
	uv run python -m mintflow.web.catalog

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f app

image-check:
	./scripts/check-production-image.sh

backup-check:
	./scripts/check-backup-roundtrip.sh
