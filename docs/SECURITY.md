# 安全边界

A.R.C.H. 将模型推理、会话状态和知识资料保留在服务主机。本页记录当前实现的安全边界和仍未完成的运行保障。

## 网络与浏览器

Caddy 是唯一允许面向局域网的浏览器入口，默认使用 `https://arch.local:8443`。FastAPI、llama.cpp、SearXNG 和 Ollama 使用回环地址或容器内部路径。浏览器状态变更要求已登录会话、精确 HTTPS Origin 和 CSRF token；不要改用裸 IP 访问入口。

客户端必须信任该部署的 Caddy 本地 CA，并将 `arch.local` 加入代理绕过列表。不要复制 `deploy/caddy-data/`，其中可能包含私钥和 Caddy 状态。详细 LAN 设置见[让局域网设备访问](how-to/lan-access.md)。

## 凭据与数据

管理员密码只以 Argon2id 哈希保存于 `data/auth/admin.json`。SearXNG 密钥存于 `data/runtime/searxng.env`，脚本要求它为 `0600`。不要将密码、cookie、私钥、数据库、模型、日志、备份或运行时密钥提交到 Git。

`ARCH_DATA_DIR` 与 `ARCH_ARTIFACT_DIR` 受应用限制，只能位于仓库 `data/` 中。应用状态由该目录保存，但 Caddy 的容器状态位于受忽略的 `deploy/caddy-data/` 与 `deploy/caddy-config/`；这两处也需要本机文件权限保护。

## 工具与搜索

模型只能请求通用 `terminal_exec`（argv）或 `terminal_shell`（脚本），并声明当前用户拥有的工作目录、读写级别及显式 capability。每个新调用都需要同一已登录浏览器会话批准；已成功完成的完全相同调用可在该会话复用 30 分钟，参数、脚本、目录、读写级别或 capability 任一变化都会再次批准。注销、服务重启、过期和用户撤销都会使授权失效。

工具以普通用户运行，保留固定 PATH、输出上限、10 秒时限和 `NoNewPrivileges`。读取调用在用户级 systemd 进程隔离中启动；所有自动执行均已禁用，不能把模型声称的 `read` 当作权限证明。`.ssh`、`.aws`、`.gnupg`、`.config`、`.docker`、`.kube` 等敏感目录及提权/namespace 入口仍被拒绝。`hardware` 仅为显式硬件查询开放最小 NVIDIA 设备节点；Docker capability 即使读取状态也只允许“主机操作”档位并需浏览器确认。联网搜索仍默认关闭，只允许后端访问 loopback SearXNG。

## 运行限制

后端 user unit 使用 `CPUQuota`、`MemoryHigh`、`MemoryMax`、`TasksMax` 和 `LimitNOFILE`；其默认值
位于 `.env.example`，安装器只接受受限格式的覆盖值。Caddy、SearXNG 和可选 Ollama 使用 Compose
CPU/内存上限与 Docker `local` 日志轮转。后端、网关和容器 healthcheck 只确认启动边界，不表示
模型、登录、浏览器媒体、真实扬声器或人工插话已验收。用户 journal 的保留策略由主机管理员配置，
本仓库不会修改全局 journald 配置。运维启动和恢复边界见[运行操作](OPERATIONS.md)。
