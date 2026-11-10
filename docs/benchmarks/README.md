# Milestone 1 Benchmarks

For repeatable browser-path acceptance evidence, see [browser voice acceptance](acceptance.md). It is separate from component baselines and does not establish audible device latency.

The first model combination is a measured baseline, not a product default. The selected candidate configuration is in `config/benchmark-baseline.json`.

`models-baseline` is the only command that may download the approved benchmark sample. `llm-prepare`, `llamacpp-prepare`, `asr-prepare`, and `tts-prepare` are separate, explicit commands for the pinned Qwen3 GGUF, CUDA llama.cpp runtime, Faster-Whisper model, and Kokoro Chinese ONNX model plus voice pack. `ollama-import` and `ollama-pull` remain available for diagnostics and later adapters. Model weights must remain under `data/models/`, and every accepted result must record an exact source revision and SHA256. Normal lint, test, build, and development commands never download a model or a sample.

Run every component twenty times after one warm-up. The ASR report records transcription latency, the Ollama report records first generated content and server-reported tokens per second, and `kokoro_benchmark.py` records warm synthesis plus in-memory WAV availability. `pipeline_benchmark.py` creates only an additive pre-integration budget; it does not replace Milestone 2 browser playback measurements.

Record benchmark JSON beneath `docs/benchmarks/results/`, then summarize model revisions, GPU memory, cold versus warm behavior, p50, p95, maximum, and failed runs in `docs/benchmarks/voice-baseline.md`. Do not commit audio samples, model weights, or private transcripts.

The initial Mandarin sample is listed in `config/benchmark-samples.json`. It is a 10.38-second FLEURS recording at a pinned dataset revision. The manifest records its CC-BY-4.0 license, public source, transcript, and SHA-256; the downloaded WAV remains under ignored `data/benchmarks/samples/`.

The ASR baseline is `dropbox-dash/faster-whisper-large-v3-turbo` at revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`. `asr-prepare` verifies the Git LFS SHA-256 for `model.bin` before writing local provenance. The LLM baseline is the official `Qwen/Qwen3-8B-GGUF` Q4_K_M file at revision `6a569868d07d3bd59e8b97fb001bf8c0b254bb20`; `llm-prepare` verifies its LFS SHA-256. `llamacpp-prepare` obtains official llama.cpp build `b12110` for CUDA 12.8, then `llama-server` exposes the model only at `127.0.0.1:8081`. The Ollama service receives proxy variables only when they are supplied to the Compose command; it remains loopback-only.

Faster-Whisper 1.2.1 requires `metadata_errors` in `av.open`; PyAV 19 removed that parameter. The benchmark group therefore fixes PyAV at 18.1.0. Do not upgrade it above 18 until Faster-Whisper has removed that call and the GPU benchmark has been repeated. The Kokoro baseline deliberately uses CPU ONNX Runtime because the current environment already resolves a CPU ONNX Runtime through Pipecat; do not add `onnxruntime-gpu` without revalidating the shared dependency tree and measurements.

The benchmark scripts run through `uv --directory backend`, so paths passed to them are relative to `backend/`. For example, use `--audio ../data/benchmarks/samples/fleurs_cmn_hans_cn_0000.wav` and write raw reports below `../data/benchmarks/results/`.
