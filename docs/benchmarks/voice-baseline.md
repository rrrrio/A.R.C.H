# 语音基线记录

日期：2026-11-08。所有结果来自 Ubuntu 24.04、RTX 4090 24 GB、Ryzen 7 9700X、32 GB 内存。原始机器特定 JSON 位于受忽略的 `data/benchmarks/results/`。

## 固定输入

中文 ASR 样本为 FLEURS `cmn_hans_cn_0000`，数据集 revision `1cca811bb8ea4d370345f108f00518167040282c`，CC-BY-4.0，16 kHz 单声道、10.38 秒。SHA-256 为 `5214e16584b6498ddbd22f321738c0f62b19117ab1880b21e405fba983986b6a`。参考文本是“这并不是告别这是一个篇章的结束也是新篇章的开始”。

LLM 使用固定中文提示：“用一句中文说明本机语音助手已启动。不要展示推理过程。”

## ASR

运行时是 Faster-Whisper 1.2.1、CTranslate2 4.8.5、PyAV 18.1.0。模型为 `dropbox-dash/faster-whisper-large-v3-turbo` revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`，`model.bin` SHA-256 为 `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da`。配置为 CUDA FP16、beam size 1、中文、一次 warm-up 后 20 次运行。

| 指标 | 结果 |
| --- | ---: |
| warm-up 转录 | 175.0 ms |
| 推理 p50 | 80.8 ms |
| 推理 p95 | 86.0 ms |
| 推理最大值 | 86.3 ms |
| 模型驻留显存 | 2494 MiB |

转录结果为“这并不是告别,这是一个篇章的结束,也是新篇章的开始”，与公开参考文本语义一致。

## LLM

运行时是官方 llama.cpp build `b12110`（commit `cd959bae99ff4bed22bef2604f45241c09bbb8c8`），使用 CUDA 12.8 打包运行时。模型为官方 `Qwen/Qwen3-8B-GGUF` Q4_K_M，revision `6a569868d07d3bd59e8b97fb001bf8c0b254bb20`，GGUF SHA-256 为 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`。服务仅监听 `127.0.0.1:8081`，使用 OpenAI 兼容流式接口、`--n-gpu-layers 99`、`--jinja` 和 `--reasoning-format deepseek`，一次 warm-up 后 20 次运行。

| 指标 | 结果 |
| --- | ---: |
| 首段内容 p50 | 32.8 ms |
| 首段内容 p95 | 33.4 ms |
| 总响应 p50 | 76.9 ms |
| 总响应 p95 | 77.9 ms |
| 生成速率 p50 | 130.1 字符/秒 |
| 模型驻留显存 | 10812 MiB |

## 尚未完成

TTS 基线仍未锁定。Kokoro 中文 ONNX 是首选候选，但必须先固定模型 revision、许可证证据和 SHA-256，随后测量完整 WAV 可用时间与句子级首音延迟。端到端 speech-end 到浏览器首音时间只能在 Milestone 2 的真实媒体流水线中确认。
