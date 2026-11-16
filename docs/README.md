# 文档导航

本目录描述 `A.R.C.H.`（Autonomous Reasoning & Conversation Hub）当前已实现的本地浏览器语音系统。系统只允许一个已登录的浏览器语音会话；模型、数据库、知识资料和凭据均保存在仓库的 `data/` 下。固定 MP4 真人视频数字人的鉴权视频桥接、MuseTalk 增量 renderer 和 preview worker 已实现，并完成主机 IPC/GPU/WebRTC track 与 Chrome 视频解码 smoke；远端浏览器设备上的持续帧率和音画同步尚未验收。

## 按任务阅读

- [首次在本机完成一次对话](tutorials/first-local-conversation.md)：新手从依赖、模型、HTTPS 到浏览器语音会话的线性教程。
- [让局域网设备访问](how-to/lan-access.md)：将同一 `/23` 网段内的设备安全接入 HTTPS 网关。
- [通过已有穿透网络访问](how-to/routed-network-access.md)：让已经能到达 LAN 的远程浏览器使用同一语音页面。
- [启用或关闭联网搜索](how-to/enable-web-search.md)：启动本地 SearXNG，并保持它不暴露到局域网。
- [管理本地知识库](how-to/manage-knowledge.md)：放入资料、建立索引、确认删除生效。
- [管理审批记忆](how-to/manage-memory.md)：审阅、批准、验证和删除本机长期偏好与事实。
- [管理本地声音参考](how-to/manage-voice-profiles.md)：保存经确认授权的常见格式短音频，并选择本机克隆声音。
- [使用本机主机 Agent](how-to/use-host-agent.md)：选择终端权限档位、审阅精确调用授权并撤销活动 scope。
- [准备可选 Qwen 声音克隆运行时](how-to/prepare-qwen-tts.md)：先审阅无写入计划，再明确下载、校验和隔离可选制品。
- [备份和恢复本地数据](how-to/backup-and-restore.md)：创建经校验的数据库和知识资料归档，并安全恢复。
- [运行浏览器语音冒烟验收](how-to/browser-voice-smoke-test.md)：用固定本地 WAV 验证完整浏览器语音链路。
- [测量目标设备语音验收](how-to/measure-device-voice-acceptance.md)：在第二台 LAN 设备上记录可听首音和人工插话的 20 轮 p95。
- [运行操作](OPERATIONS.md)：当前服务的启动、停止、检查和恢复边界。
- [卸载](UNINSTALL.md)：预览并删除生成物，默认保留用户数据。
- [安全边界](SECURITY.md)：网络、凭据、数据、工具与当前运行限制。
- [模型与许可证](MODELS_AND_LICENSES.md)：固定模型来源、准备命令、许可证证据与可复现边界。
- [配置参考](reference/configuration.md)：环境变量、端口、持久化目录和支持的文件格式。
- [架构与安全边界](explanation/architecture.md)：实时路径、即时插话取消（包括 PCM 回退）、审批、记忆、RAG 与视频数字人的设计理由。
- [使用浏览器数字人预览](how-to/use-browser-avatar.md)：在不上传 VRM 模型的前提下启用本地浏览器预览。
- [使用真人视频数字人](how-to/use-video-avatar.md)：准备固定 MP4、启动独立视频 worker 和验证视频 WebRTC。
- [开发环境](DEVELOPMENT.md)：运行测试、启动开发服务和维护文档。
- [组件基准](benchmarks/README.md)：当前 ASR、LLM 和 TTS 基线及其复跑方式，以及浏览器语音验收证据的边界。

## 当前运行边界

浏览器必须使用配置的 HTTPS 域名访问，例如 `https://arch.local:8443`。不要直接访问 `https://192.168.10.126:8443`：现有 Caddy 站点和本地证书只为该域名签发，且应用会核验精确的浏览器 origin。局域网客户端必须将该域名解析到服务主机的 LAN IP、信任本地 CA，并绕过任何上游代理。

当前声学流水线以中文为主：Silero VAD、Faster-Whisper、Qwen3 8B 与 Kokoro 中文声线均在本机运行。登录后的浏览器以“未来语音舱”为主工作区：声场核心、连接操作、字幕和麦克风选择保持在首屏；从导航可进入“记忆”“知识库”“审批”“声音”“主机 Agent”和“数字人”。切换这些管理区不会断开当前语音会话。页面能够显示语音状态、字幕、阶段延迟、记忆候选、知识库状态和工具审批；经已有穿透网络路由的浏览器已完成一次完整对话验收。
