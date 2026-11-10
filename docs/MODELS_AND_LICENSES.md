# 模型与许可证

本页列出当前固定基线的来源、准备命令和上游许可证标识。它不是法律意见；部署、再分发或商用前应复核所下载 revision 的上游 LICENSE 与依赖许可证。

| 组件 | 准备命令 | 固定来源 | 许可证证据 |
| --- | --- | --- | --- |
| ASR | `make asr-prepare` | `dropbox-dash/faster-whisper-large-v3-turbo`，revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | 本地模型卡标为 MIT。 |
| LLM | `make llm-prepare` | `Qwen/Qwen3-8B-GGUF` Q4_K_M，revision `6a569868d07d3bd59e8b97fb001bf8c0b254bb20` | [上游模型卡](https://huggingface.co/Qwen/Qwen3-8B-GGUF) 标为 Apache-2.0。 |
| llama.cpp | `make llamacpp-prepare` | build `b12110`，commit `cd959bae99ff4bed22bef2604f45241c09bbb8c8` | [上游仓库](https://github.com/ggml-org/llama.cpp) 为 MIT；本地运行时附带 LICENSE。 |
| TTS | `make tts-prepare` | `onnx-community/Kokoro-82M-v1.1-zh-ONNX`，revision `6cc0f0d2ebe369a68b0df87c2b65c1af8c0ac3e3` | 当前固定模型与声包记录为 Apache-2.0；`kokoro-onnx` Python 包为 MIT。 |
| 可选克隆 TTS | 尚未提供准备命令 | `Qwen/Qwen3-TTS-12Hz-0.6B-Base`，计划固定 revision `c27fe8aa05b732b1376d0f6a1e522fbccb84abbd` | [上游模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) 标为 Apache-2.0；官方 `qwen-tts` 0.1.1 包也标为 Apache-2.0。当前未下载、安装或启用。 |
| RAG 嵌入 | `make rag-prepare` | `BAAI/bge-m3`，revision `5617a9f61b028005a4858fdac845db406aefb181` | [上游模型卡](https://huggingface.co/BAAI/bge-m3) 标为 MIT。 |
| 基准音频 | `make models-baseline` | FLEURS `cmn_hans_cn_0000`，revision `1cca811bb8ea4d370345f108f00518167040282c` | CC-BY-4.0，详见 `config/benchmark-samples.json`。 |

ASR、固定 GGUF 和 TTS 路径会验证列出的 artifact SHA-256；ASR、固定 GGUF、RAG 和 TTS 路径会写入 `lva-provenance.json`。llama.cpp 与基准音频分别保留 hash 验证事实，但当前不写该 provenance 文件。模型、声包和基准音频均保持在受 Git 忽略的 `data/` 内；不要提交它们。

`make ollama-pull` 拉取的是可变 `qwen3:8b` tag，未记录 revision 或 artifact hash。它只能用于诊断或后续适配器试验，不能替代上述固定 LLM 基线。
