# 安全边界

A.R.C.H. 将模型推理、会话状态和知识资料保留在服务主机。本页记录当前实现的安全边界和仍未完成的运行保障。

## 网络与浏览器

Caddy 是唯一允许面向局域网的浏览器入口，默认使用 `https://arch.local:8443`。FastAPI、llama.cpp、SearXNG 和 Ollama 使用回环地址或容器内部路径。浏览器状态变更要求已登录会话、精确 HTTPS Origin 和 CSRF token；不要改用裸 IP 访问入口。

客户端必须信任该部署的 Caddy 本地 CA，并将 `arch.local` 加入代理绕过列表。不要复制 `deploy/caddy-data/`，其中可能包含私钥和 Caddy 状态。详细 LAN 设置见[让局域网设备访问](how-to/lan-access.md)。

## 凭据与数据

管理员密码只以 Argon2id 哈希保存于 `data/auth/admin.json`。SearXNG 密钥存于 `data/runtime/searxng.env`，脚本要求它为 `0600`。不要将密码、cookie、私钥、数据库、模型、日志、备份或运行时密钥提交到 Git。

`ARCH_DATA_DIR` 与 `ARCH_ARTIFACT_DIR` 受应用限制，只能位于仓库 `data/` 中。应用状态由该目录保存，但 Caddy 的容器状态位于受忽略的 `deploy/caddy-data/` 与 `deploy/caddy-config/`；这两处也需要本机文件权限保护。

## 工具与搜索

终端工具默认拒绝。低风险只读命令受工作目录、超时和输出限制；写入操作需要一次性浏览器批准，高风险操作不能被批准绕过。联网搜索默认关闭，启用时只允许后端访问 loopback SearXNG；搜索请求会离开本机，但摘要仍由本地模型处理。

## 运行限制

后端 user unit 使用 `CPUQuota`、`MemoryHigh`、`MemoryMax`、`TasksMax` 和 `LimitNOFILE`；其默认值
位于 `.env.example`，安装器只接受受限格式的覆盖值。Caddy、SearXNG 和可选 Ollama 使用 Compose
CPU/内存上限与 Docker `local` 日志轮转。后端、网关和容器 healthcheck 只确认启动边界，不表示
模型、登录、浏览器媒体、真实扬声器或人工插话已验收。用户 journal 的保留策略由主机管理员配置，
本仓库不会修改全局 journald 配置。运维启动和恢复边界见[运行操作](OPERATIONS.md)。
