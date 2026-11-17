# 运行操作

本页说明当前 A.R.C.H. 部署中实际可用的启动、停止、检查和恢复命令。仓库可生成后端和 Caddy 的用户级 systemd unit，但不会自动安装或启用它们；SearXNG 和 Ollama 仍是按需 Compose profile。后端、网关和可选容器现在都有就绪或健康检查，并提供针对目标 32 GiB / RTX 4090 主机的可覆盖资源与容器日志上限；这些默认值尚未通过真实设备或重启验收。

当前服务的依赖关系如下。`/api/health` 只证明后端 HTTP 路由可响应，不检查模型、数据库、Caddy 或搜索服务是否就绪。

```text
[backend /api/ready: 127.0.0.1:8000] <-- Caddy --> [browser: LAN HTTPS :8443]
          ^                                  |
          |                                  +--> frontend/dist
          |
          +-- local llama.cpp child process during a voice session

[Avatar worker: data/runtime/avatar/avatar.sock] <-- optional video only
[SearXNG: 127.0.0.1:8080] <-- optional web_search
[Ollama: 127.0.0.1:11434]  <-- optional model adapter
```

systemd 的就绪门槛如下。后端已完成本地目录、SQLite migration 和应用存储初始化后才回答
`/api/ready`；Caddy 容器健康后，网关 unit 还会通过 HTTPS 入口请求同一后端路由。可选
Ollama 和 SearXNG 不阻塞后端就绪，因为它们不是每次语音会话的必需依赖。

```text
[arch-backend.service] --> [/api/ready]
          |                     |
          v                     v
      restart on failure   [arch-gateway.service]
                                  |
                          compose up --wait caddy
                                  |
                                  v
                       HTTPS loopback /api/ready

[SearXNG /healthz] and [ollama list] are optional container health checks.
```

## 启动浏览器入口

先准备前端和数据库：

    make frontend-build
    make migrate

在一个终端启动后端：

    cd backend
    uv run uvicorn arch.main:app --host 127.0.0.1 --port 8000

在另一个终端启动 Caddy：

    make gateway-up

检查后端存活和已完成启动的就绪状态：

    curl http://127.0.0.1:8000/api/health
    curl http://127.0.0.1:8000/api/ready

检查 HTTPS 入口时，使用 Caddy 本地 CA 和配置的域名；详见[首次在本机完成一次语音对话](tutorials/first-local-conversation.md)。局域网客户端的名称解析、CA 信任和代理绕过步骤见[让局域网设备访问](how-to/lan-access.md)。

停止后端时，在它所在的终端按 `Ctrl-C`。停止 Caddy：

    make gateway-down

查看 Caddy 日志：

    make gateway-logs

## 可选服务

联网搜索默认关闭。启用步骤、启动和停止 SearXNG 的命令见[启用或关闭联网搜索](how-to/enable-web-search.md)。Ollama 仅是可选本地模型适配器：

    make ollama-up

仓库没有对应的 `make` 停止目标；确认不再需要它后，可运行：

    docker compose -f deploy/compose.yaml --profile models stop ollama

## 用户级 systemd

先查看将要写入的 unit：

    make systemd-dry-run

确认后安装到当前用户的 `~/.config/systemd/user/`：

    make systemd-install APPLY=1
    systemctl --user daemon-reload
    systemctl --user enable --now arch-backend.service arch-gateway.service

旧的 `arch-avatar.service` JPEG bridge 已停用，不能重新启用。安装器也会渲染新的
`arch-avatar-media.service`，但当前单 peer 音视频替换尚未完成前不得启用它作为用户功能。
若在维护环境检查 unit 渲染，先在受限的 `.env` 设置独立高熵的
`ARCH_AVATAR_MEDIA_TICKET_SECRET`；该服务只绑定 `127.0.0.1:8101`，不经 Caddy 暴露。
普通语音不依赖两个 avatar unit。

新 runtime 的 Wav2Lip cache 必须由与真人素材匹配的 source video 和 checkpoint 生成。该维护命令只调用固定 checkout 的人脸检测模块，不启动 LiveTalking 的 HTTP、TTS 或 WebRTC 服务，也不会在完整帧中写入 `LiveTalking` 水印：

    make avatar-media-wav2lip-prepare

运行新 runtime 后，运维人员可在受控服务日志中筛选 `avatar-media-timing`。事件仅用于计算提交、渲染、首包、浏览器首帧、取消和回退的耗时；不得把它与 PCM、帧、文本、ticket 或浏览器身份以外的内容关联。安装或启用 loopback unit 不会发布浏览器视频功能，不能用该日志替代目标设备验收。

runtime 会在每次已准备回答的首个音频/视频样本之后，分别按 20ms 音频 chunk 和 25fps 视频时钟交付，避免浏览器收到突发帧。取消会结束两条等待或播放中的轨道并拒绝已取消回答的迟到渲染；这一主机级保证仍须由已部署浏览器和目标设备验收。

命令需要 CUDA 和已授权的 `data/avatars/idle.mp4`、`data/runtime/livetalking/wav2lip.pth`。输出位于受忽略的 `data/runtime/avatar-media/avatars/`。它尚不表示视频数字人已可用；当前公开状态仍为“重建中”。

安装器默认拒绝覆盖现有 unit。已有 backend、gateway 或旧 avatar unit 时，可只安装替换 runtime：

    uv run --no-sync --directory backend python scripts/install_user_services.py --apply --unit arch-avatar-media.service

确认本机修改后的同名 unit 需要替换时，使用 `SYSTEMD_FLAGS=--replace`。`arch-backend.service` 在失败时重启，并在 `/api/ready` 成功前视为未启动；`arch-gateway.service` 等待 Caddy 容器健康，再检查 HTTPS 到后端的同一路径。它们均不启动搜索或 Ollama。

后端日志进入用户 journal：

    journalctl --user -u arch-backend.service -n 100 --no-pager
    journalctl --user -u arch-avatar-media.service -n 100 --no-pager

Compose 容器使用 Docker `local` 日志驱动，每个容器最多保留三个 10 MiB 文件。Caddy 的
`127.0.0.1:2019/healthz` 仅供容器健康检查使用，不是局域网入口，也不应转发或暴露给浏览器。

## 资源与容器健康

`.env.example` 为目标 32 GiB / RTX 4090 主机提供初始资源上限：后端为 800% CPU、10 GiB
`MemoryHigh`、12 GiB `MemoryMax` 和 512 个任务；可选 Ollama 为 8 CPU / 12 GiB，SearXNG
为 2 CPU / 1 GiB，Caddy 为 1 CPU / 512 MiB。请在启用 systemd 或 Compose 前，根据实际
模型加载、并发和可用内存调整 `.env` 的 `ARCH_*_CPU_*` 与 `ARCH_*_MEMORY_*` 值，再重新渲染
unit（必要时 `SYSTEMD_FLAGS=--replace`）。安装器拒绝包含换行或非单位格式的后端资源值。

容器健康可查看：

    docker compose -f deploy/compose.yaml ps

`healthy` 只证明相应服务能回答其本地探针；它不替代浏览器登录、模型加载、麦克风、可听首音
或人工插话验收。

avatar worker 另有 8 GiB `MemoryHigh`、12 GiB `MemoryMax` 和 512 个任务的 unit 上限；实际 MuseTalk
显存峰值必须在目标主机上单独测量。视频服务失败时，音频服务应继续可用。

## 备份与恢复

在升级、替换数据或处理磁盘问题之前先创建归档：

    make backup

归档只在本机 `data/backups/` 中创建，必须复制到受控的离机位置才构成独立备份。恢复、校验和替换保护步骤见[备份和恢复本地数据](how-to/backup-and-restore.md)。

## 升级

先停止后端和 Caddy，再预览升级动作：

    make upgrade-dry-run

确认后执行：

    make upgrade APPLY=1

该命令先创建完整数据库和 inbox 归档，再只应用待处理的 SQLite 向前 migration。当前没有自动 down migration；每个 migration 独立提交，失败时之前已成功的版本可能仍保留。若迁移失败或需要回退，停止服务并按[备份和恢复本地数据](how-to/backup-and-restore.md)从该归档恢复，再切回与归档兼容的代码版本。升级后运行 `make rag-index`，然后启动服务并检查登录、记忆和知识库状态。

## 当前验收边界

自动化测试覆盖应用逻辑、归档校验和隔离浏览器媒体路径。当前尚未完成主机重启恢复、第二台 LAN 设备完整语音通话、真实可听首音或人工插话延迟的设备验收。不要把 HTTP 健康响应或隔离 Chrome 结果当作这些验收的替代品。
