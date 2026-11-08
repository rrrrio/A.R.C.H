SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help bootstrap lint test backend-test frontend-test frontend-build compose-config dev migrate gateway-up gateway-down gateway-logs search-provision search-up search-down search-logs browser-e2e models-baseline benchmark-components benchmark-pipeline ollama-up ollama-pull ollama-import asr-prepare llm-prepare llamacpp-prepare tts-prepare rag-prepare rag-index benchmark-tts

help:
	@printf '%s\n' 'Targets: bootstrap lint test backend-test frontend-test frontend-build compose-config dev migrate gateway-up gateway-down gateway-logs search-provision search-up search-down search-logs browser-e2e models-baseline benchmark-components benchmark-pipeline ollama-up ollama-pull ollama-import asr-prepare llm-prepare llamacpp-prepare tts-prepare rag-prepare rag-index benchmark-tts'

bootstrap:
	uv sync --directory $(BACKEND_DIR) --all-groups
	npm --prefix $(FRONTEND_DIR) ci

lint:
	uv run --directory $(BACKEND_DIR) ruff check .
	uv run --no-sync --directory $(BACKEND_DIR) mypy
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

migrate:
	uv run --directory $(BACKEND_DIR) python scripts/migrate.py

gateway-up: frontend-build
	$(COMPOSE) --profile gateway up -d caddy

gateway-down:
	$(COMPOSE) --profile gateway stop caddy

gateway-logs:
	$(COMPOSE) --profile gateway logs --tail=100 caddy

search-provision:
	uv run --directory $(BACKEND_DIR) python scripts/provision_searxng_secret.py

search-up: search-provision
	$(COMPOSE) --profile search up -d searxng

search-down:
	$(COMPOSE) --profile search stop searxng

search-logs:
	$(COMPOSE) --profile search logs --tail=100 searxng

browser-e2e: frontend-build
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/run_browser_voice_e2e.py

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

tts-prepare:
	uv run --directory $(BACKEND_DIR) python spikes/prepare_tts_model.py

rag-prepare:
	uv run --directory $(BACKEND_DIR) python scripts/prepare_rag_model.py

rag-index:
	HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --directory $(BACKEND_DIR) python scripts/index_knowledge.py

benchmark-tts:
	@printf '%s\n' 'Run tts_benchmark.py with run_kokoro_tts.py and the pinned files under data/models/kokoro-zh/.'

asr-prepare:
	uv run --directory $(BACKEND_DIR) python spikes/prepare_asr_model.py

benchmark-components:
	@printf '%s\n' 'Run backend/spikes/asr_benchmark.py, llm_benchmark.py, and tts_benchmark.py with explicit local inputs and write JSON under docs/benchmarks/results/.'

benchmark-pipeline:
	@printf '%s\n' 'Run backend/spikes/pipeline_benchmark.py with three existing component JSON reports.'
