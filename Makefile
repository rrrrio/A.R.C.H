SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help bootstrap lint test backend-test frontend-test frontend-build compose-config dev models-baseline benchmark-components benchmark-pipeline ollama-up ollama-pull ollama-import asr-prepare llm-prepare llamacpp-prepare

help:
	@printf '%s\n' 'Targets: bootstrap lint test backend-test frontend-test frontend-build compose-config dev models-baseline benchmark-components benchmark-pipeline ollama-up ollama-pull ollama-import asr-prepare llm-prepare llamacpp-prepare'

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

models-baseline:
	uv run --directory $(BACKEND_DIR) python spikes/download_benchmark_sample.py
	@printf '%s\n' 'Run make llm-prepare then make ollama-import for the pinned GGUF. Run make asr-prepare for ASR. TTS preparation remains explicit until its ONNX revision is pinned.'

ollama-up:
	$(COMPOSE) --profile models up -d ollama

ollama-pull: ollama-up
	$(COMPOSE) exec ollama ollama pull qwen3:8b

llm-prepare:
	uv run --directory $(BACKEND_DIR) python spikes/prepare_llm_model.py

ollama-import: ollama-up
	$(COMPOSE) exec ollama ollama create qwen3-8b-local -f /modelfiles/qwen3-8b.Modelfile

llamacpp-prepare:
	uv run --directory $(BACKEND_DIR) python spikes/prepare_llama_cpp.py

asr-prepare:
	uv run --directory $(BACKEND_DIR) python spikes/prepare_asr_model.py

benchmark-components:
	@printf '%s\n' 'Run backend/spikes/asr_benchmark.py, llm_benchmark.py, and tts_benchmark.py with explicit local inputs and write JSON under docs/benchmarks/results/.'

benchmark-pipeline:
	@printf '%s\n' 'Run backend/spikes/pipeline_benchmark.py with three existing component JSON reports.'
