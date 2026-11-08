SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help bootstrap lint test backend-test frontend-test frontend-build compose-config dev

help:
	@printf '%s\n' 'Targets: bootstrap lint test backend-test frontend-test frontend-build compose-config dev'

bootstrap:
	uv sync --directory $(BACKEND_DIR) --all-groups
	npm --prefix $(FRONTEND_DIR) ci

lint:
	uv run --directory $(BACKEND_DIR) ruff check .
	uv run --directory $(BACKEND_DIR) mypy src tests
	npm --prefix $(FRONTEND_DIR) run lint

test: backend-test frontend-test

backend-test:
	uv run --directory $(BACKEND_DIR) pytest

frontend-test:
	npm --prefix $(FRONTEND_DIR) run test -- --run

frontend-build:
	npm --prefix $(FRONTEND_DIR) run build

compose-config:
	$(COMPOSE) config --quiet

dev:
	@printf '%s\n' 'Start the backend with: cd backend && uv run uvicorn local_voice_agent.main:app --host 127.0.0.1 --port 8000'
	@printf '%s\n' 'Start the frontend with: cd frontend && npm run dev'
