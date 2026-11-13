SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

BACKEND_DIR := backend
FRONTEND_DIR := frontend
COMPOSE := docker compose -f deploy/compose.yaml

.PHONY: help bootstrap lint test backend-test frontend-test frontend-build compose-config dev migrate upgrade-dry-run upgrade backup restore systemd-dry-run systemd-install uninstall-dry-run uninstall gateway-up gateway-down gateway-logs search-provision search-up search-down search-logs browser-e2e browser-e2e-barge-in acceptance-audio models-baseline benchmark-components benchmark-pipeline benchmark-voice-clone avatar-check avatar-preview avatar-musetalk-smoke avatar-browser-smoke musetalk-dry-run ollama-up ollama-pull ollama-import asr-prepare llm-prepare llamacpp-prepare tts-prepare qwen-tts-dry-run qwen-tts-prepare rag-prepare rag-index benchmark-tts

help:
	@printf '%s\n' 'Targets: bootstrap lint test backend-test frontend-test frontend-build compose-config dev migrate upgrade-dry-run upgrade backup restore systemd-dry-run systemd-install uninstall-dry-run uninstall gateway-up gateway-down gateway-logs search-provision search-up search-down search-logs browser-e2e browser-e2e-barge-in acceptance-audio models-baseline benchmark-components benchmark-pipeline benchmark-voice-clone avatar-check avatar-preview avatar-musetalk-smoke avatar-browser-smoke musetalk-dry-run ollama-up ollama-pull ollama-import asr-prepare llm-prepare llamacpp-prepare tts-prepare qwen-tts-dry-run qwen-tts-prepare rag-prepare rag-index benchmark-tts'

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
	@printf '%s\n' 'Start the backend with: cd backend && uv run uvicorn arch.main:app --host 127.0.0.1 --port 8000'
	@printf '%s\n' 'Start the frontend with: cd frontend && npm run dev'

migrate:
	uv run --directory $(BACKEND_DIR) python scripts/migrate.py

upgrade-dry-run:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/upgrade.py

upgrade:
	@test "$(APPLY)" = "1" || (printf '%s\n' 'Set APPLY=1 after reviewing make upgrade-dry-run.' >&2; exit 2)
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/upgrade.py --apply

backup:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/backup.py

restore:
	@test -n "$(ARCHIVE)" || (printf '%s\n' 'Set ARCHIVE to an A.R.C.H. backup archive.' >&2; exit 2)
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/restore.py "$(ARCHIVE)" $(RESTORE_FLAGS)

systemd-dry-run:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/install_user_services.py

systemd-install:
	@test "$(APPLY)" = "1" || (printf '%s\n' 'Set APPLY=1 after reviewing make systemd-dry-run.' >&2; exit 2)
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/install_user_services.py --apply $(SYSTEMD_FLAGS)

uninstall-dry-run:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/uninstall.py

uninstall:
	@test "$(APPLY)" = "1" || (printf '%s\n' 'Set APPLY=1 after reviewing make uninstall-dry-run.' >&2; exit 2)
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/uninstall.py --apply $(UNINSTALL_FLAGS)

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

browser-e2e-barge-in: frontend-build
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/run_browser_voice_e2e.py --assert-barge-in

acceptance-audio: frontend-build
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/summarize_browser_voice_acceptance.py --output ../data/benchmarks/results/browser-voice-normal.json
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/summarize_browser_voice_acceptance.py --barge-in --output ../data/benchmarks/results/browser-voice-barge-in.json

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

qwen-tts-dry-run:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/prepare_qwen_tts.py

qwen-tts-prepare:
	@test "$(APPLY)" = "1" || (printf '%s\n' 'Set APPLY=1 after reviewing make qwen-tts-dry-run.' >&2; exit 2)
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/prepare_qwen_tts.py --apply

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

benchmark-voice-clone:
	@test -n "$(VOICE_PROFILE_ID)" || (printf '%s\n' 'Set VOICE_PROFILE_ID to a consented local voice profile.' >&2; exit 2)
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/benchmark_qwen_clone.py --profile-id "$(VOICE_PROFILE_ID)" --output ../data/benchmarks/results/qwen-clone.json

avatar-check:
	uv run --directory $(BACKEND_DIR) python scripts/prepare_avatar.py

avatar-preview:
	ARCH_VIDEO_AVATAR_ENABLED=true ARCH_AVATAR_WORKER_BACKEND=preview uv run --project avatar_runtime arch-avatar-worker --socket data/runtime/avatar/avatar.sock --source data/avatars/idle.mp4 --fps 25 --backend preview

avatar-musetalk:
	PYTHONPATH=avatar_runtime/src data/runtime/musetalk/bin/python -m avatar_runtime.worker --socket data/runtime/avatar/avatar.sock --source data/avatars/idle.mp4 --fps 25 --backend musetalk --model-root data/models/musetalk/2bcb936e2fddb4d86db4c62fd45b387d0c061571 --cache-dir data/runtime/avatar/preprocessed --musetalk-source data/runtime/musetalk/source

avatar-musetalk-prepare:
	PYTHONPATH=avatar_runtime/src data/runtime/musetalk/bin/python -m avatar_runtime.prepare --source data/avatars/idle.mp4 --fps 25 --model-root data/models/musetalk/2bcb936e2fddb4d86db4c62fd45b387d0c061571 --cache-dir data/runtime/avatar/preprocessed --musetalk-source data/runtime/musetalk/source

avatar-musetalk-smoke:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/smoke_musetalk_avatar.py

avatar-browser-smoke:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/run_browser_avatar_e2e.py

musetalk-dry-run:
	uv run --no-sync --directory $(BACKEND_DIR) python scripts/prepare_musetalk.py
