# A.R.C.H.

**Autonomous Reasoning & Conversation Hub**

A.R.C.H. 是一个本地优先的实时语音 Agent 运行时。它把语音识别、语言模型、语音合成、会话记忆、知识检索和工具编排放在同一台机器上，并通过一个经过认证的 HTTPS 入口提供浏览器语音会话。

A.R.C.H. 面向希望掌控数据和运行环境的个人用户。普通语音会话不依赖云端推理；联网搜索是默认关闭、由用户主动调用的可选工具。

> 当前状态：积极开发中。实时语音链路、认证、Agent Core、审批记忆、本地 RAG 和受限工具已实现，并有自动化测试和隔离 Chrome 冒烟路径。第二台 LAN 设备的真实听感、可听首音和人工插话延迟仍未完成设备验收。音色克隆、情绪控制和数字人后端目前只保留扩展接口。

## 路线图

这里只保留公开方向和当前状态，详细实施计划不放在 README 中。

- 第二台 LAN 设备的真实语音验收，包括可听首音和人工插话延迟（已实现但未设备验证）
- 离线、恢复、备份和长期运行流程（计划中）
- 音色克隆与情绪控制，同时保持低延迟语音路径可回退（计划中）
- 真实数字人后端及音画同步验收（计划中）

## 能力

- **实时语音**：浏览器麦克风经过 HTTPS 和 WebRTC 进入本地 VAD、ASR、LLM 和 TTS 流水线；媒体条件受限时可回退到同源 WebSocket PCM 通道。
- **可替换的 Agent Core**：Pipecat 负责媒体帧和流式处理，项目自己的 Agent Core 负责上下文、记忆、RAG、工具请求和响应事件。
- **可控的长期记忆**：模型生成的事实和偏好先进入待确认列表，只有用户批准后才会影响后续会话。
- **本地知识库**：只索引 `data/knowledge/inbox/` 中用户明确放入的文件，并保留来源路径和索引状态。
- **受限工具**：低风险只读命令可以自动执行；写入或其他状态变更需要一次性浏览器批准，高风险命令直接拒绝。
- **局域网访问**：Caddy 提供唯一的 HTTPS 入口，后端、模型和搜索服务默认只绑定回环地址。

## 架构

下图展示实时路径、后台上下文和插话取消的边界。

```text
[Browser]
    | authenticated HTTPS + WebRTC
    v
[Caddy] --> [FastAPI / Pipecat]
                         |
                         +--> VAD --> ASR --> [Agent Core] --> local LLM
                         |                         |
                         |                         +--> memory / RAG context
                         |                         +--> ToolGateway
                         |                               +--> terminal
                         |                               +--> optional web search
                         |
                         +<-- TTS PCM <---- [OutputMux] --> browser audio

user speech-start --> cancel active utterance
                      --> LLM stream, TTS queue, browser output, avatar adapter
```

媒体层不拥有长期记忆、权限策略或数字人业务。`OutputMux` 和 `AvatarAdapter` 让未来的音色和数字人实现可以替换输出层，而不迁移用户数据或重写 Agent Core。

## 快速开始

### 前置条件

- Ubuntu 24.04 或相近的 Linux 环境
- Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js/npm 和 Docker
- 支持本地模型推理的 NVIDIA GPU

### 验证工作区

在仓库根目录执行：

```bash
cp .env.example .env
make bootstrap
make frontend-build
make test
```

这些命令会安装依赖、构建前端并运行自动化测试。它们不会下载语音模型，也不会启动服务。要完成一次本地语音会话，请继续阅读[首次在本机完成一次语音对话](docs/tutorials/first-local-conversation.md)。

如果模型已经准备好，可以运行隔离的浏览器语音冒烟测试：

```bash
make browser-e2e
```

该命令会临时启动后端、Caddy、Chrome 和本地模型服务，验证登录、WebRTC、用户转录、助手转录和服务器首段音频事件。它不能替代真实设备的听感、LAN 访问或人工插话验收，完整边界见[浏览器语音冒烟验收](docs/how-to/browser-voice-smoke-test.md)。

## 安全与数据边界

- 运行时可写数据都位于 `data/`，包括凭据、数据库、模型、知识库和日志。
- Caddy 是唯一的 LAN 入口；FastAPI、llama.cpp、Ollama 和 SearXNG 默认只监听回环地址。
- 联网搜索默认关闭。启用后，只有搜索请求会离开本机，摘要仍由本地模型生成。
- 终端工具使用受限工作目录、明确的命令策略、超时和输出上限；浏览器批准不能绕过硬拒绝规则。
- 不要将密码、证书私钥、模型、数据库、日志或搜索服务密钥提交到 Git。

## 文档

完整文档按教程、操作指南、参考和架构说明分类，统一从[文档导航](docs/README.md)进入。根目录 README 只保留稳定的核心入口：

- [首次在本机完成一次语音对话](docs/tutorials/first-local-conversation.md)：准备依赖、模型、管理员密码、证书并完成首次通话。
- [让局域网设备访问](docs/how-to/lan-access.md)：配置域名解析、CA 信任、代理绕过和防火墙。
- [配置参考](docs/reference/configuration.md)：环境变量、端口、数据目录和支持的文件格式。
- [架构与安全边界](docs/explanation/architecture.md)：实时路径、取消、记忆、RAG 与工具权限的设计理由。
- [开发环境](docs/DEVELOPMENT.md)：测试、构建、开发服务和文档维护。

## 开发

常用质量检查：

```bash
make lint
make test
make frontend-build
make compose-config
```

后端包名为 `arch`。本地服务、数据库迁移、模型准备、搜索服务和浏览器验收命令见[开发环境](docs/DEVELOPMENT.md)与[配置参考](docs/reference/configuration.md)。

## 仓库结构

```text
arch/
├── backend/   Python API、语音流水线、Agent Core、记忆、RAG 和工具
├── frontend/  浏览器界面、麦克风、字幕和审批状态
├── deploy/    Caddy、Compose 和本地搜索服务配置
├── data/      模型、数据库和运行时数据，不提交到 Git
└── docs/      教程、操作指南、参考和架构说明
```
