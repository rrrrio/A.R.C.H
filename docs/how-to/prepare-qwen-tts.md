# 准备可选 Qwen 声音克隆运行时

Qwen 声音克隆是可选路径。当前浏览器语音会话仍使用 Kokoro；只有完成此准备、后续运行时接入和设备验收后，已保存的声音参考才会参与合成。

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

准备成功会让独立 Python 环境能够启动仅监听 Unix socket 的本地 Qwen worker；它不暴露 TCP 端口，参考文件也只由 profile ID 在本机解析。该 worker 尚未由浏览器会话选择，因此准备成功不代表克隆运行时已接入 A.R.C.H.，也不证明声音相似度、
授权充分性、GPU 余量、可听首音、插话取消或第二台设备体验。继续保留 Kokoro 作为可用基线，直到目标设备上的克隆路径完成测量。
