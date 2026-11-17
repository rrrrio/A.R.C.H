# 配置参考

本文是当前环境变量、网络边界、数据目录和知识库输入格式的事实参考。部署步骤见[首次在本机完成一次语音对话](../tutorials/first-local-conversation.md)，局域网接入步骤见[让局域网设备访问](../how-to/lan-access.md)。

## 目录

- [解说](#解说)
- [参考场景](#参考场景)
- [组件与参数说明](#组件与参数说明)
  - [组件说明](#组件说明)
  - [参数说明](#参数说明)
- [网络端口](#网络端口)
- [数据目录](#数据目录)
- [知识库文件格式](#知识库文件格式)

## 解说

应用从仓库根目录 `.env` 读取 `ARCH_` 前缀的变量；未设置时使用下表默认值。为兼容已有部署，后端也接受对应的 `LVA_` 变量，但新配置应使用 `ARCH_`。`ARCH_DATA_DIR` 和可选的 `ARCH_ARTIFACT_DIR` 都必须为仓库 `data/` 或其子目录，越界路径会让后端拒绝启动。后者只为模型和运行时制品提供固定读取位置，不能替代 `ARCH_DATA_DIR` 的用户状态写入边界。`ARCH_PUBLIC_ORIGIN` 是浏览器进行登录、语音信令、记忆和工具审批时必须匹配的完整 HTTPS origin；它必须与 `ARCH_LAN_HOST`、`ARCH_HTTPS_PORT` 共同组成同一地址。

## 参考场景

```gherkin
Feature: Browser origin configuration
  Scenario: Browser uses the configured HTTPS name
    Given ARCH_PUBLIC_ORIGIN is https://arch.local:8443
    And ARCH_LAN_HOST is arch.local
    And ARCH_HTTPS_PORT is 8443
    When a browser opens https://arch.local:8443
    Then authenticated state-changing requests have the accepted Origin

  Scenario: A non-loopback search endpoint is configured
    Given ARCH_SEARXNG_ENDPOINT is http://192.168.10.126:8080
    When the backend starts
    Then configuration validation rejects the endpoint
```

## 组件与参数说明

### 组件说明

后端 `Settings` 优先读取 `ARCH_` 前缀变量，并兼容读取同字段的 `LVA_` 旧变量。Caddy 从相同 `.env` 读取 LAN 主机名和 HTTPS 端口；这两个变量不改变后端监听地址。搜索变量仅在后端启动时生效，SearXNG 容器还需要独立的受忽略运行时密钥文件。

```yaml
application:
  data_dir: data
  artifact_dir: null
  public_origin: https://arch.local:8443
  search:
    enabled: false
    endpoint: http://127.0.0.1:8080
gateway:
  lan_host: arch.local
  https_port: 8443
```

### 参数说明

| 参数 | 类型 / 默认值 | 约束 | 作用 |
| --- | --- | --- | --- |
| `ARCH_DATA_DIR` | `Path` / `data` | 必须在仓库 `data/` 内 | 应用模型、凭据、数据库、知识库、日志和运行时状态的唯一写入根目录；不包含 Caddy 容器的受忽略状态目录。 |
| `ARCH_ARTIFACT_DIR` | `Path` / 未设置 | 未设置时等于 `ARCH_DATA_DIR`；设置时必须在仓库 `data/` 内 | 固定模型和运行时制品的读取根目录；隔离验收用它读取已有制品，同时将凭据与 SQLite 写入独立 `ARCH_DATA_DIR`。 |
| `ARCH_ENVIRONMENT` | `str` / `development` | 非空字符串 | 健康检查中返回的环境标签。 |
| `ARCH_HOST` | `str` / `127.0.0.1` | 合法监听地址 | 后端开发服务器的默认监听主机；生产入口由 Caddy 代理。 |
| `ARCH_PORT` | `int` / `8000` | 1 到 65535 | 后端默认端口。 |
| `ARCH_PUBLIC_ORIGIN` | `str` / `https://arch.local:8443` | 无路径、查询或片段的 HTTPS origin | 登录、CSRF 和状态变更 API 接受的精确浏览器 origin。 |
| `ARCH_LAN_HOST` | `str` / `arch.local` | Caddy 可签发证书的主机名 | HTTPS 网关的站点主机名。 |
| `ARCH_HTTPS_PORT` | `int` / `8443` | 有效 TCP 端口 | Caddy 的 HTTPS 监听端口。 |
| `ARCH_SEARCH_ENABLED` | `bool` / `false` | 布尔值 | 是否创建本地 SearXNG 搜索客户端。 |
| `ARCH_SEARXNG_ENDPOINT` | `str` / `http://127.0.0.1:8080` | 仅 `127.0.0.1`、`localhost` 或 `::1`；不得含路径、查询或凭据 | 本地 SearXNG JSON 服务。 |
| `ARCH_VIDEO_AVATAR_ENABLED` | `bool` / `false` | 布尔值；默认关闭 | 仅当此值为真、ticket secret 已配置且 loopback runtime 正在运行时，已认证浏览器才显示可用的视频开关；失败时普通语音保持可用。 |
| `ARCH_AVATAR_MEDIA_RUNTIME_URL` | `str` / `http://127.0.0.1:8101` | 只能是无路径、查询或凭据的 `http://127.0.0.1` origin | 后端向单 peer 音视频运行时转发已认证信令的私有地址；不会返回给浏览器。该值本身不启用视频。 |
| `ARCH_AVATAR_MEDIA_TICKET_SECRET` | `SecretStr` / 未设置 | 至少 16 UTF-8 字节；应为独立高熵值；不得提交 | 后端和 loopback runtime 共同验证的一次性、短期 ticket 密钥。未设置时 runtime 拒绝启动，后端返回视频不可用。 |
| `ARCH_AVATAR_MEDIA_BACKEND` | `str` / 未设置 | 浏览器视频当前仅接受 `wav2lip` | `arch-avatar-media.service` 必须设为 `wav2lip` 才会通过私有 health check 并向浏览器显示可用。 |
| `ARCH_AVATAR_RUNTIME_DIR` | `Path` / `data/runtime/avatar` | 必须在 `data/` 内 | 独立 avatar worker 的运行状态目录。 |
| `ARCH_AVATAR_SOCKET_PATH` | `Path` / `data/runtime/avatar/avatar.sock` | 必须在 `data/` 内 | A.R.C.H. 后端与 GPU worker 之间的 Unix socket。 |
| `ARCH_AVATAR_SOURCE_VIDEO` | `Path` / `data/avatars/idle.mp4` | 必须在 `data/` 内，必须有可解码视频帧 | 管理员提供的固定真人参考视频。 |
| `ARCH_AVATAR_MODEL_ROOT` | `Path` / `data/models/musetalk/2bcb936e2fddb4d86db4c62fd45b387d0c061571` | 必须在 `data/` 内；只读 | 固定 MuseTalk V1.5 权重与附属模型目录。 |
| `ARCH_AVATAR_CACHE_DIR` | `Path` / `data/runtime/avatar/preprocessed` | 必须在 `data/` 内；worker 可写 | 由固定 MP4 内容、模型 revision 和裁切参数寻址的预处理缓存根目录。 |
| `ARCH_AVATAR_MUSETALK_SOURCE` | `Path` / `data/runtime/musetalk/source` | 必须在 `data/` 内；只读 | 隔离 CUDA runtime 使用的固定 MuseTalk 源码 checkout。 |
| `ARCH_AVATAR_WAV2LIP_SOURCE` | `Path` / `data/runtime/livetalking/source` | 必须在 `data/` 内；只读 | 成片模式使用的 LiveTalking Wav2Lip 源码 checkout；只加载口型模块，不启动其 HTTP 或 WebRTC 服务。 |
| `ARCH_AVATAR_WAV2LIP_MODEL` | `Path` / `data/runtime/livetalking/wav2lip.pth` | 必须在 `data/` 内；只读 | 运维方自行提供且已核验使用权的 Wav2Lip checkpoint。 |
| `ARCH_AVATAR_WAV2LIP_AVATAR` | `Path` / `data/runtime/livetalking/avatars/wav2lip_idle_reference` | 必须在 `data/` 内；只读；含 `coords.pkl`、`full_imgs/` 和 `face_imgs/` | 必须由 `ARCH_AVATAR_SOURCE_VIDEO` 生成的同一人物预处理头像缓存，不能与不同人物或不同裁切参数混用。 |
| `data/runtime/avatar-media/avatars/` | runtime cache root / 默认由准备命令写入 | 受 Git 忽略；每项由 source/checkpoint 内容、25fps、256px crop、pads 和 generator revision 定址 | 新 AvatarMediaRuntime 的无水印 Wav2Lip cache。准备器拒绝不完整或不匹配的 cache，浏览器和 API 不会暴露该路径。 |
| `ARCH_AVATAR_MODEL` | `str` / `musetalk-1.5` | 由独立 worker 解释 | 视频口型模型标识。 |
| `ARCH_AVATAR_WORKER_BACKEND` | `str` / `musetalk` | `musetalk`、`wav2lip` 或 `preview` | `musetalk` 为低延迟增量口型；`wav2lip` 等待一次 TTS 播报结束后生成完整片段以换取连续成片观感；`preview` 仅循环固定视频。 |
| `ARCH_AVATAR_FPS` | `int` / `25` | 1 到 60 | 视频轨道目标帧率。 |
| `ARCH_AVATAR_QUEUE_MAX_MS` | `int` / `500` | 100 到 5000 | 视频帧和 PCM 迟到时使用的有界队列预算。 |
| `ARCH_AVATAR_STARTUP_TIMEOUT_MS` | `int` / `15000` | 1000 到 120000 | avatar worker 启动等待上限。 |
| `ARCH_AVATAR_VRAM_BUDGET_MB` | `int` / `22000` | 1024 到 24564 | 目标 RTX 4090 上为语音模型保留余量的显存预算记录。 |
| `ARCH_BACKEND_CPU_QUOTA` | `str` / `800%` | 正整数加 `%`；由 systemd 安装器渲染 | 后端 user unit 的 CPU 上限。 |
| `ARCH_BACKEND_MEMORY_HIGH` | `str` / `10G` | 正整数加 `K`、`M`、`G` 或 `T`；不得高于 `ARCH_BACKEND_MEMORY_MAX` | 后端开始回收内存的 systemd 阈值。 |
| `ARCH_BACKEND_MEMORY_MAX` | `str` / `12G` | 正整数加 `K`、`M`、`G` 或 `T` | 后端 user unit 的硬内存上限。 |
| `ARCH_BACKEND_TASKS_MAX` | `int` / `512` | 正整数 | 后端 user unit 可创建的最大任务数。 |
| `ARCH_OLLAMA_CPU_LIMIT` / `ARCH_OLLAMA_MEMORY_MAX` | `str` / `8.0`、`12g` | Docker Compose 可接受的 CPU / 内存值 | 可选 Ollama 容器上限。 |
| `ARCH_CADDY_CPU_LIMIT` / `ARCH_CADDY_MEMORY_MAX` | `str` / `1.0`、`512m` | Docker Compose 可接受的 CPU / 内存值 | Caddy 容器上限。 |
| `ARCH_SEARXNG_CPU_LIMIT` / `ARCH_SEARXNG_MEMORY_MAX` | `str` / `2.0`、`1g` | Docker Compose 可接受的 CPU / 内存值 | 可选 SearXNG 容器上限。 |

旧部署中的 `LVA_*` 变量与上述 `ARCH_*` 变量一一对应。若同一字段同时设置两种名称，使用 `ARCH_*` 值。

## 网络端口

| 服务 | 地址 | 是否可由 LAN 访问 | 用途 |
| --- | --- | --- | --- |
| Caddy | `*:8443` | 是 | 唯一浏览器 HTTPS 入口；提供前端并代理 `/api/*`。 |
| FastAPI 后端 | `127.0.0.1:8000` | 否 | 登录、信令、记忆、知识库和工具 API。 |
| AvatarMediaRuntime | `127.0.0.1:8101` | 否 | 仅接收后端携带的一次性 ticket 的私有音视频会话控制面；不提供浏览器、LAN 或 OpenAPI 入口。 |
| llama.cpp | `127.0.0.1:8081` | 否 | 会话启动时由后端拥有的本地 LLM 服务。 |
| SearXNG | `127.0.0.1:8080` | 否 | 可选本地搜索聚合服务。 |
| Ollama | `127.0.0.1:11434` | 否 | 保留的本地模型适配器，不是当前语音会话默认 LLM。 |
| Caddy 容器健康 | `127.0.0.1:2019` | 否 | 仅 Caddy 容器的 loopback `/healthz` 探针；不供浏览器或 LAN 客户端使用。 |

## 数据目录

| 路径 | 内容 | 保留与安全边界 |
| --- | --- | --- |
| `data/auth/admin.json` | 管理员 Argon2id 密码哈希 | 受 Git 忽略；不要复制或提交。 |
| `data/db/arch.sqlite3` | 会话、记忆、审批和知识索引 | 受 Git 忽略；备份前先停止写入或使用一致性备份流程。新文件不存在时，应用继续使用旧部署的 `data/db/local-voice-agent.sqlite3`。若两者都存在，优先使用 `arch.sqlite3`。 |
| `data/knowledge/inbox/` | 用户可索引资料 | 唯一允许的 RAG 源目录。 |
| `data/voices/profiles/` | 经确认授权的本地 WAV 参考及元数据 | 多声道或非 WAV 上传由后端 FFmpeg 转为单声道 24 kHz、16-bit PCM WAV；已符合约束的单声道 WAV 可直接保存。不进入数据库与知识资料备份归档，不得提交、共享或记录到日志。 |
| `data/runtime/avatar-media/avatars/` | 无水印 Wav2Lip source cache | 由 `make avatar-media-wav2lip-prepare` 在维护环境生成；包含完整帧、256px face crop、坐标和 manifest。浏览器不会暴露该路径。 |
| `data/backups/` | `make backup` 创建的压缩归档 | 归档含 SQLite 一致性副本和知识资料 inbox，不含管理员凭据、模型或本地 CA；应复制到受控的离机位置。 |
| `data/models/` | 固定模型、声线和嵌入模型 | 受 Git 忽略；运行时按固定路径验证。 |
| `data/models/faster-whisper/<revision>/` | 本地 Faster-Whisper ASR 模型 | 由 `make asr-prepare` 准备；用于实时语音识别和声音参考文本草稿，不得提交或共享。 |
| `data/models/qwen3-tts/<revision>/` | 可选 Qwen Base 模型、speech tokenizer 与 provenance | 仅由 `make qwen-tts-prepare APPLY=1` 写入；不存在或未验证时不得被未来克隆运行时使用。 |
| `data/runtime/` | llama.cpp 和 SearXNG 运行时状态 | 受 Git 忽略；`searxng.env` 必须为 `0600`。 |
| `data/runtime/qwen-tts/` | 可选 Qwen TTS 的隔离 Python 环境 | 仅由明确准备命令创建；不属于当前 A.R.C.H. 后端依赖环境。 |
| `data/workspace/` | 主机 Agent 的默认工作区 | 相对目录以这里解析；工作目录必须归当前用户所有，敏感路径始终拒绝。所有新调用均需浏览器审批。 |

## 主机 Agent 授权

模型只能调用 `terminal_exec`（argv）或 `terminal_shell`（脚本），并同时声明工作目录、读写级别和可选 `network`、`hardware`、`service`、`docker` capability。省略工作目录时使用 `data/workspace/`；本地模型产生的固定占位值 `/home/user` 会在工作目录或 argv 路径参数中规范化为当前服务用户的真实 home。经批准的只读调用可使用 `/` 作为工作目录来检查根文件系统。没有自然语言关键字或预置任务目录；GPU 查询、项目检查和文件排序都经同一接口校验。

每个首次调用都在浏览器中显示完整 argv 或脚本、目录、读写级别与 capability。批准成功的完全相同调用仅在该登录会话中复用 30 分钟；登录结束、服务重启、过期或撤销都会使其失效。Docker 即使是状态查询也要求“主机操作”档位及浏览器确认。详情见[使用本机主机 Agent](../how-to/use-host-agent.md)。

## 知识库文件格式

| 项目 | 值 |
| --- | --- |
| 根目录 | `data/knowledge/inbox/` |
| 支持后缀 | `.txt`、`.md`、`.pdf`、`.py`、`.json`、`.yaml`、`.yml` |
| 单文件上限 | 10 MiB |
| 文本编码 | UTF-8；PDF 由本地解析器提取文本 |
| 跳过项 | 符号链接、非普通文件、超大文件、损坏 PDF、空文本和不支持格式 |
| 更新方式 | `make rag-index` 基于内容 hash 增量同步；删除源文件后再次运行会删除对应索引。 |
