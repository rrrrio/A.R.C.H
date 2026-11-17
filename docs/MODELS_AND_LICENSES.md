# 模型与许可证

本页列出当前固定基线的来源、准备命令和上游许可证标识。它不是法律意见；部署、再分发或商用前应复核所下载 revision 的上游 LICENSE 与依赖许可证。

| 组件 | 准备命令 | 固定来源 | 许可证证据 |
| --- | --- | --- | --- |
| ASR | `make asr-prepare` | `dropbox-dash/faster-whisper-large-v3-turbo`，revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | 本地模型卡标为 MIT。 |
| LLM | `make llm-prepare` | `Qwen/Qwen3-8B-GGUF` Q4_K_M，revision `6a569868d07d3bd59e8b97fb001bf8c0b254bb20` | [上游模型卡](https://huggingface.co/Qwen/Qwen3-8B-GGUF) 标为 Apache-2.0。 |
| llama.cpp | `make llamacpp-prepare` | build `b12110`，commit `cd959bae99ff4bed22bef2604f45241c09bbb8c8` | [上游仓库](https://github.com/ggml-org/llama.cpp) 为 MIT；本地运行时附带 LICENSE。 |
| TTS | `make tts-prepare` | `onnx-community/Kokoro-82M-v1.1-zh-ONNX`，revision `6cc0f0d2ebe369a68b0df87c2b65c1af8c0ac3e3` | 当前固定模型与声包记录为 Apache-2.0；`kokoro-onnx` Python 包为 MIT。 |
| 可选克隆 TTS | `make qwen-tts-dry-run`，确认后 `make qwen-tts-prepare APPLY=1` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base`，revision `c27fe8aa05b732b1376d0f6a1e522fbccb84abbd` | [上游模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) 标为 Apache-2.0；官方 `qwen-tts` 0.1.1 包也标为 Apache-2.0。准备会校验发布的模型和 speech tokenizer SHA-256；已接入本地隔离 worker，但尚未设备验收。 |
| RAG 嵌入 | `make rag-prepare` | `BAAI/bge-m3`，revision `5617a9f61b028005a4858fdac845db406aefb181` | [上游模型卡](https://huggingface.co/BAAI/bge-m3) 标为 MIT。 |
| 基准音频 | `make models-baseline` | FLEURS `cmn_hans_cn_0000`，revision `1cca811bb8ea4d370345f108f00518167040282c` | CC-BY-4.0，详见 `config/benchmark-samples.json`。 |
| 视频口型 | `make musetalk-dry-run`；维护窗口中 `make avatar-musetalk` | [MuseTalk](https://github.com/TMElyralab/MuseTalk) 源码 `0a89dec45a0192b824e3cf4daf96c239440c5ed8`；V1.5 UNet 来自 `TMElyralab/MuseTalk` revision `2bcb936e2fddb4d86db4c62fd45b387d0c061571`。本机使用固定 VAE `31f26fdeee1355a5c34592e401dd41e45d25a493`、Whisper tiny `169d4a4341b33bc18d8881c4b69c2e104e1cc0af`、DWPose 和 face-parse checkpoint。 | MuseTalk 与 VAE 上游为 MIT；Whisper 为 Apache-2.0，DWPose 为 Apache-2.0。face-parse checkpoint 的再分发许可证尚待上游确认，因此仅限本机运维准备，不得作为可再分发基线。 |
| 视频口型（成片模式） | 维护窗口中 `make avatar-wav2lip-smoke` | 本机固定的 LiveTalking checkout `c7269433a2b6ecebcd9342445d2ecc882c98b0da` 中仅使用 `avatars/wav2lip` 模块；checkpoint 和头像缓存由运维方从其受权来源放入 `data/runtime/livetalking/`。 | LiveTalking checkout 内的 LICENSE 为 Apache-2.0。下载包附带的 Wav2Lip checkpoint 与头像缓存没有可复现的上游来源或再分发许可证证据，不纳入 A.R.C.H. 固定模型基线，禁止随仓库或部署制品再分发。 |

ASR、固定 GGUF 和 TTS 路径会验证列出的 artifact SHA-256；ASR、固定 GGUF、RAG 和 TTS 路径会写入 `lva-provenance.json`。llama.cpp 与基准音频分别保留 hash 验证事实，但当前不写该 provenance 文件。模型、声包和基准音频均保持在受 Git 忽略的 `data/` 内；不要提交它们。

`make ollama-pull` 拉取的是可变 `qwen3:8b` tag，未记录 revision 或 artifact hash。它只能用于诊断或后续适配器试验，不能替代上述固定 LLM 基线。

下载原型中的 `wav2lip256.pth` 和 `wav2lip256_avatar1.tar.gz` 不属于 A.R.C.H. 的固定模型基线。`idle.mp4` 是运维提供的真人素材，不随仓库再分发；使用者必须确认肖像、声音和模型权利。
