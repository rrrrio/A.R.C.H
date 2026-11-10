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

## TTS

运行时是 `kokoro-onnx` 0.6.1、Misaki 0.9.6 和 CPU ONNX Runtime 1.31.1。模型为 `onnx-community/Kokoro-82M-v1.1-zh-ONNX` revision `6cc0f0d2ebe369a68b0df87c2b65c1af8c0ac3e3`，`onnx/model_fp16.onnx` SHA-256 为 `d59cb626c885c91acb7ecf7250f26f9915ad0af99b770572000d90b598c3dfbc`。语音包为项目发布的 `voices-v1.1-zh.bin`，SHA-256 为 `14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd`，使用 `zf_001` 声线。模型和语音包采用 Apache-2.0；Python 包本身采用 MIT。

固定输入为“你好，这是本地语音基准测试。”，输出为 24 kHz PCM。模型与中文 G2P 常驻后先 warm-up 一次，再运行 20 次。计时分别记录 ONNX 合成，以及把结果编码为内存 PCM WAV 后的可用时间；不包含磁盘写入、网络传输、浏览器抖动缓冲或播放。

复跑命令如下；原始机器相关 JSON 仍只写入受忽略的 `data/benchmarks/results/`：

```bash
uv run --directory backend python spikes/kokoro_benchmark.py \
  --model ../data/models/kokoro-zh/6cc0f0d2ebe369a68b0df87c2b65c1af8c0ac3e3/onnx/model_fp16.onnx \
  --voice-file ../data/models/kokoro-zh/6cc0f0d2ebe369a68b0df87c2b65c1af8c0ac3e3/voices-v1.1-zh.bin \
  --runs 20 --output ../data/benchmarks/results/tts-kokoro-zh.json
```

| 指标 | 结果 |
| --- | ---: |
| 合成 p50 | 50.6 ms |
| 合成 p95 | 54.0 ms |
| 内存 WAV 可用 p50 | 50.8 ms |
| 内存 WAV 可用 p95 | 54.3 ms |
| 合成 RTF p50 | 0.168 |
| 合成 RTF p95 | 0.180 |
| 合成音频时长 | 300.7 ms |

`kokoro-onnx` 0.6.1 需要包含全部中文声线的聚合 `voices-v1.1-zh.bin`；单独下载的 `zf_001.bin` 与该版本的 `numpy.load` 调用不兼容。ONNX Runtime 会报告若干 `Reciprocal` 常量折叠警告，但在 20 次运行中未导致失败。

这是低延迟的句子级 CPU 基线，尚非 token/PCM 流式输出，也未证明浏览器首音或插话时间。端到端 speech-end 到浏览器首音只能在 Milestone 2 的真实媒体流水线中确认。

## 可选 Qwen 克隆 TTS

2026-11-10 在同一主机上准备 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` revision
`c27fe8aa05b732b1376d0f6a1e522fbccb84abbd` 后，隔离 worker 使用一个本地合成的 1 秒
24 kHz 静音 WAV 和对应测试文本完成一次 warm-up，再合成固定中文输入“你好，这是本地 Qwen 声音克隆
组件基准测试。”20 次。计时从本机 Unix socket 请求发出到收到完整 24 kHz PCM；它包含 Qwen 生成和
IPC 编解码，不包含 worker 模型加载、浏览器、网络、扬声器或可听时间。该测试不使用人类声音，也不测试
声音相似度。

| 指标 | 结果 |
| --- | ---: |
| 完整 PCM 可用 p50 | 3213.2 ms |
| 完整 PCM 可用 p95 | 4937.5 ms |
| 最大值 | 8300.8 ms |
| 返回 PCM 字节数 | 349440 |

该 Base 模型路径当前返回完整音频而非流式首段 PCM，p95 明显高于普通轮次 1.5 秒首音目标。因此它保持为
显式选择的可选克隆路径，Kokoro 继续作为默认低延迟声音。该组件数据不证明浏览器可听首音、插话停止、
用户许可充分性、真实音色相似度或第二台设备体验。测试结束后已删除合成 profile、worker socket 和 GPU
分配。
