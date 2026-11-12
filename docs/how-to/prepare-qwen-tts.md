# 准备可选 Qwen 声音克隆运行时

Qwen 声音克隆是可选路径。未选择克隆参考的浏览器语音会话继续使用 Kokoro；第一次选择参考并建立会话
时才启动本机隔离 Qwen worker。之后的 profile 重连会复用 Qwen worker 和已启动的本地 LLM 服务，切换回
Kokoro 只更换 TTS pipeline；应用关闭时才释放这些服务。
设备级声音相似度、可听延迟和插话验收仍未完成。

## 先查看计划

```bash
make qwen-tts-dry-run
```

该命令不下载模型、不创建 Python 环境，也不改变现有 Kokoro、数据库、知识库或声音参考。它只显示固定的 Qwen3-TTS 0.6B Base revision、受 Git 忽略的模型目录和隔离运行时目录。

## 明确准备

确认磁盘空间、网络来源和许可证边界后运行：

```bash
make qwen-tts-prepare APPLY=1
```

该命令下载官方固定 revision `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 到
`data/models/qwen3-tts/<revision>/`，验证主模型和 speech tokenizer 的发布 SHA-256，并写入
`arch-provenance.json`。随后在 `data/runtime/qwen-tts/` 创建独立 Python 环境并安装固定的
`qwen-tts==0.1.1`。普通启动、测试、构建和 Kokoro 准备命令不会执行这些步骤。

如果目标模型目录已存在却没有通过验证的 provenance，命令会拒绝覆盖它。先人工检查并移除不完整目录，
再重新执行明确准备命令；不要把模型目录、provenance 或声音参考提交到 Git。

## 当前边界

准备成功会让独立 Python 环境能够启动仅监听 Unix socket 的本地 Qwen worker；它不暴露 TCP 端口，参考文件也只由 profile ID 在本机解析。worker 和 loopback LLM 服务都由应用内的 `LocalVoiceSession` 持有，而不是由一次浏览器媒体 pipeline 持有：选择另一个 clone profile 后，新 pipeline 只更换请求中的 profile ID，复用已经加载的模型。Qwen 适配器会按句号、问号、感叹号和分号切分回答，并在每个句子完成 24 kHz PCM 后立即交给既有播放路径；没有终止标点的文本每 120 个字符强制切分。考虑到本地模型生成首帧可能超过 Pipecat 默认的 3 秒，克隆 TTS 会保留最多 30 秒的音频 context。插话仍会终止正在生成的 worker 并丢弃未播放的后续句子，因此被取消后的下一句可能重新加载模型。

已使用本地合成 WAV 完成一次 worker 启动和 24 kHz PCM 输出检查，但这不证明声音相似度、授权充分性、GPU 余量、可听首音、插话取消或第二台设备体验。继续保留 Kokoro 作为可用基线，直到目标设备上的克隆路径完成测量。

## 测量已授权的克隆 profile

创建并确认一个本地声音参考后，使用该 profile 的 UUID 运行 20 次本地 worker 基准：

```bash
make benchmark-voice-clone VOICE_PROFILE_ID=<profile-uuid>
```

命令不会创建、选择、修改或删除 profile。它只使用明确提供的现有 UUID，并将 timing-only JSON 写入受 Git 忽略的 `data/benchmarks/results/qwen-clone.json`。报告包含第一句 PCM 和完整回答 PCM 的 p50/p95/max，但不记录参考音频、profile 元数据、参考文本或请求文本。它不测量浏览器、网络、扬声器、声音相似度或人工可听延迟。
