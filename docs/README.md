# 文档导航

本目录描述 `A.R.C.H.`（Autonomous Reasoning & Conversation Hub）当前已实现的本地浏览器语音系统。系统只允许一个已登录的浏览器语音会话；模型、数据库、知识资料和凭据均保存在仓库的 `data/` 下。音色克隆、情绪控制和数字人仍在后续里程碑中，不能视为当前功能。

## 按任务阅读

- [首次在本机完成一次对话](tutorials/first-local-conversation.md)：新手从依赖、模型、HTTPS 到浏览器语音会话的线性教程。
- [让局域网设备访问](how-to/lan-access.md)：将同一 `/23` 网段内的设备安全接入 HTTPS 网关。
- [通过已有穿透网络访问](how-to/routed-network-access.md)：让已经能到达 LAN 的远程浏览器使用同一语音页面。
- [启用或关闭联网搜索](how-to/enable-web-search.md)：启动本地 SearXNG，并保持它不暴露到局域网。
- [管理本地知识库](how-to/manage-knowledge.md)：放入资料、建立索引、确认删除生效。
- [备份和恢复本地数据](how-to/backup-and-restore.md)：创建经校验的数据库和知识资料归档，并安全恢复。
- [运行浏览器语音冒烟验收](how-to/browser-voice-smoke-test.md)：用固定本地 WAV 验证完整浏览器语音链路。
- [配置参考](reference/configuration.md)：环境变量、端口、持久化目录和支持的文件格式。
- [架构与安全边界](explanation/architecture.md)：实时路径、审批、取消、记忆、RAG 与未来数字人的设计理由。
- [开发环境](DEVELOPMENT.md)：运行测试、启动开发服务和维护文档。
- [组件基准](benchmarks/README.md)：当前 ASR、LLM 和 TTS 基线及其复跑方式。

## 当前运行边界

浏览器必须使用配置的 HTTPS 域名访问，例如 `https://arch.local:8443`。不要直接访问 `https://192.168.10.126:8443`：现有 Caddy 站点和本地证书只为该域名签发，且应用会核验精确的浏览器 origin。局域网客户端必须将该域名解析到服务主机的 LAN IP、信任本地 CA，并绕过任何上游代理。

当前声学流水线以中文为主：Silero VAD、Faster-Whisper、Qwen3 8B 与 Kokoro 中文声线均在本机运行。浏览器界面能够显示语音状态、字幕、阶段延迟、记忆候选、知识库状态和工具审批；经已有穿透网络路由的浏览器已完成一次完整对话验收。
