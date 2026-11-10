# 运行浏览器语音冒烟验收

本指南验证一轮完整的本地浏览器语音路径，而不使用当前管理员账户、数据库或浏览器 profile。完成标准是命令依次报告认证、WebRTC、用户转录、助手转录和首段音频延迟，然后以零退出码结束。

## 前置条件

服务主机需要已完成 `make bootstrap`，并准备好 Faster-Whisper、Qwen3 GGUF、llama.cpp、Kokoro、BGE-M3 和基准音频。Chrome 必须位于 `/usr/bin/google-chrome`，Docker daemon 必须可用，且 GPU 在测试期间可供本地模型使用。基准音频是受 Git 忽略的 `data/benchmarks/samples/fleurs_cmn_hans_cn_0000.wav`；缺失时运行 `make models-baseline` 获取它。

测试会短时启动隔离 Uvicorn、临时 Caddy、Chrome 和 llama.cpp。避免在同一台机器上同时运行另一个使用端口 `8081` 或大量占用 GPU 的本地语音会话。

## 运行验收

在仓库根目录执行：

    make browser-e2e

预期看到：

    browser-e2e: isolated service healthy
    browser-e2e: authenticated
    browser-e2e: WebRTC connected
    browser-e2e: user transcript observed
    browser-e2e: assistant transcript and audio latency observed
    browser-e2e: passed

该命令会在 `data/e2e/<随机目录>` 创建一次性 Argon2id 管理员凭据和 SQLite 状态。它通过 `ARCH_ARTIFACT_DIR=data` 从已有制品读取固定模型和 llama.cpp 路径，不会把共享 `models` 或 `runtime` 软链接进测试目录。后端和 HTTPS 网关各使用一个随机 loopback 端口；Chrome 使用独立 profile 和进程组，将固定中文 WAV 注入伪麦克风。测试密码只经标准输入传给 Chrome 驱动，不进入命令行或日志。

## 验证清理与排查

成功后，`data/e2e/` 不应保留该次随机目录，`docker ps` 不应出现 `arch-e2e-` 容器，`ps` 不应显示测试启动的 `llama-server` 或 Chrome profile 进程。失败时可保留临时资料：

    uv run --no-sync --directory backend python scripts/run_browser_voice_e2e.py --keep-artifacts

命令会打印保留路径；检查其中的 `backend.log`，完成分析后只删除该次打印的随机目录。若在“isolated service healthy”之前失败，检查 Docker、Chrome、前端构建和模型文件。若在用户转录之后失败，检查 `backend.log` 中的 Pipecat、llama.cpp 或 Kokoro 信息。

此测试只证明 Chrome 忽略临时内部证书时的 HTTPS origin、媒体协商和服务器输出。它不证明真实设备已信任 Caddy CA、用户能够听到声音、第二台 LAN 设备可用，或人工插话满足延迟目标；这些仍须按[让局域网设备访问](lan-access.md)进行手工验收。
