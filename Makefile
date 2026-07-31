.PHONY: setup format lint typecheck test check run hooks docker-up docker-down docker-logs

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
	uv run uvicorn --factory mintflow.main:create_app --reload

hooks:
	uv run pre-commit run --all-files

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f app
