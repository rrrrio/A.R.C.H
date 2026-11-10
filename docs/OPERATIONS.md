# 运行操作

本页说明当前 A.R.C.H. 部署中实际可用的启动、停止、检查和恢复命令。仓库可生成后端和 Caddy 的用户级 systemd unit，但不会自动安装或启用它们；SearXNG 和 Ollama 仍是按需 Compose profile。统一的全服务启动、容器 healthcheck、资源限制和日志轮转尚未提供。

当前服务的依赖关系如下。`/api/health` 只证明后端 HTTP 路由可响应，不检查模型、数据库、Caddy 或搜索服务是否就绪。

```text
[manual backend: 127.0.0.1:8000] <-- Caddy --> [browser: LAN HTTPS :8443]
          ^                                  |
          |                                  +--> frontend/dist
          |
          +-- local llama.cpp child process during a voice session

[SearXNG: 127.0.0.1:8080] <-- optional web_search
[Ollama: 127.0.0.1:11434]  <-- optional model adapter
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

检查后端 HTTP 路由：

    curl http://127.0.0.1:8000/api/health

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

安装器默认拒绝覆盖现有 unit。确认本机修改后的 unit 需要替换时，使用 `SYSTEMD_FLAGS=--replace`。`arch-backend.service` 在失败时重启；`arch-gateway.service` 只保持 Caddy Compose 服务处于启动状态。它们均不启动搜索或 Ollama。

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
