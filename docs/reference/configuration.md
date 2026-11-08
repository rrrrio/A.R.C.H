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

应用从仓库根目录 `.env` 读取 `LVA_` 前缀的变量；未设置时使用下表默认值。`LVA_DATA_DIR` 必须为仓库 `data/` 或其子目录，越界路径会让后端拒绝启动。`LVA_PUBLIC_ORIGIN` 是浏览器进行登录、语音信令、记忆和工具审批时必须匹配的完整 HTTPS origin；它必须与 `LVA_LAN_HOST`、`LVA_HTTPS_PORT` 共同组成同一地址。

## 参考场景

```gherkin
Feature: Browser origin configuration
  Scenario: Browser uses the configured HTTPS name
    Given LVA_PUBLIC_ORIGIN is https://local-voice-agent.local:8443
    And LVA_LAN_HOST is local-voice-agent.local
    And LVA_HTTPS_PORT is 8443
    When a browser opens https://local-voice-agent.local:8443
    Then authenticated state-changing requests have the accepted Origin

  Scenario: A non-loopback search endpoint is configured
    Given LVA_SEARXNG_ENDPOINT is http://192.168.10.126:8080
    When the backend starts
    Then configuration validation rejects the endpoint
```

## 组件与参数说明

### 组件说明

后端 `Settings` 只读取 `LVA_` 前缀变量。Caddy 从相同 `.env` 读取 LAN 主机名和 HTTPS 端口；这两个变量不改变后端监听地址。搜索变量仅在后端启动时生效，SearXNG 容器还需要独立的受忽略运行时密钥文件。

```yaml
application:
  data_dir: data
  public_origin: https://local-voice-agent.local:8443
  search:
    enabled: false
    endpoint: http://127.0.0.1:8080
gateway:
  lan_host: local-voice-agent.local
  https_port: 8443
```

### 参数说明

| 参数 | 类型 / 默认值 | 约束 | 作用 |
| --- | --- | --- | --- |
| `LVA_DATA_DIR` | `Path` / `data` | 必须在仓库 `data/` 内 | 模型、凭据、数据库、知识库、日志和运行时状态的唯一写入根目录。 |
| `LVA_ENVIRONMENT` | `str` / `development` | 非空字符串 | 健康检查中返回的环境标签。 |
| `LVA_HOST` | `str` / `127.0.0.1` | 合法监听地址 | 后端开发服务器的默认监听主机；生产入口由 Caddy 代理。 |
| `LVA_PORT` | `int` / `8000` | 1 到 65535 | 后端默认端口。 |
| `LVA_PUBLIC_ORIGIN` | `str` / `https://local-voice-agent.local:8443` | 无路径、查询或片段的 HTTPS origin | 登录、CSRF 和状态变更 API 接受的精确浏览器 origin。 |
| `LVA_LAN_HOST` | `str` / `local-voice-agent.local` | Caddy 可签发证书的主机名 | HTTPS 网关的站点主机名。 |
| `LVA_HTTPS_PORT` | `int` / `8443` | 有效 TCP 端口 | Caddy 的 HTTPS 监听端口。 |
| `LVA_SEARCH_ENABLED` | `bool` / `false` | 布尔值 | 是否创建本地 SearXNG 搜索客户端。 |
| `LVA_SEARXNG_ENDPOINT` | `str` / `http://127.0.0.1:8080` | 仅 `127.0.0.1`、`localhost` 或 `::1`；不得含路径、查询或凭据 | 本地 SearXNG JSON 服务。 |

## 网络端口

| 服务 | 地址 | 是否可由 LAN 访问 | 用途 |
| --- | --- | --- | --- |
| Caddy | `*:8443` | 是 | 唯一浏览器 HTTPS 入口；提供前端并代理 `/api/*`。 |
| FastAPI 后端 | `127.0.0.1:8000` | 否 | 登录、信令、记忆、知识库和工具 API。 |
| llama.cpp | `127.0.0.1:8081` | 否 | 会话启动时由后端拥有的本地 LLM 服务。 |
| SearXNG | `127.0.0.1:8080` | 否 | 可选本地搜索聚合服务。 |
| Ollama | `127.0.0.1:11434` | 否 | 保留的本地模型适配器，不是当前语音会话默认 LLM。 |

## 数据目录

| 路径 | 内容 | 保留与安全边界 |
| --- | --- | --- |
| `data/auth/admin.json` | 管理员 Argon2id 密码哈希 | 受 Git 忽略；不要复制或提交。 |
| `data/db/local-voice-agent.sqlite3` | 会话、记忆、审批和知识索引 | 受 Git 忽略；备份前先停止写入或使用一致性备份流程。 |
| `data/knowledge/inbox/` | 用户可索引资料 | 唯一允许的 RAG 源目录。 |
| `data/models/` | 固定模型、声线和嵌入模型 | 受 Git 忽略；运行时按固定路径验证。 |
| `data/runtime/` | llama.cpp 和 SearXNG 运行时状态 | 受 Git 忽略；`searxng.env` 必须为 `0600`。 |
| `data/workspace/` | 受限终端工具的唯一工作根目录 | 工具策略拒绝其外部路径。 |

## 知识库文件格式

| 项目 | 值 |
| --- | --- |
| 根目录 | `data/knowledge/inbox/` |
| 支持后缀 | `.txt`、`.md`、`.pdf`、`.py`、`.json`、`.yaml`、`.yml` |
| 单文件上限 | 10 MiB |
| 文本编码 | UTF-8；PDF 由本地解析器提取文本 |
| 跳过项 | 符号链接、非普通文件、超大文件、损坏 PDF、空文本和不支持格式 |
| 更新方式 | `make rag-index` 基于内容 hash 增量同步；删除源文件后再次运行会删除对应索引。 |
