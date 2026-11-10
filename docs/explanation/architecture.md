# 架构与安全边界

## 为什么浏览器只连接一个 HTTPS 入口

浏览器需要安全上下文才能采集麦克风，且登录 cookie、CSRF 和 WebRTC 信令必须共享同一 origin。因此 Caddy 是唯一面向 LAN 的服务：它在 `8443` 提供静态前端，并把 `/api/*` 转发给仅监听 `127.0.0.1:8000` 的 FastAPI 后端。模型、搜索和数据库不直接暴露给局域网。

`arch.local` 不是可选显示名，而是当前 TLS 证书、Caddy 站点和 `ARCH_PUBLIC_ORIGIN` 的共同标识。用 IP 地址替代它会改变 TLS SNI 和浏览器 origin，导致证书或请求校验失败。局域网部署应让 DNS 或客户端 hosts 把该名称解析到服务主机，而不应改用裸 IP。

## 实时语音路径

实时路径与持久化和工具操作分离。Pipecat 负责浏览器媒体帧；项目自己的 Agent Core 管理上下文、记忆、检索和工具。这样未来更换 TTS、音色克隆或数字人时，不必让它们获得数据库或终端权限。

    Browser microphone
           |
      HTTPS + WebRTC（优先）
           v
    Caddy -> FastAPI signaling -> Pipecat worker
                                   |
                     VAD -> ASR -> Agent Core -> TTS
                                   |                 |
                       memory / RAG / tools          v
                                              OutputMux -> browser audio
                                                           \
                                                            -> future avatar

当前输入与输出分别为 16 kHz 和 24 kHz。ASR 使用本地 Faster-Whisper；会话创建时启动受后端拥有的 loopback llama.cpp；TTS 使用 Kokoro 中文声线。Qwen3 请求与 llama.cpp 服务均关闭 reasoning，避免推理内容占满实时工具调用的输出预算。

当 WebRTC 信令成功但服务器没有收到麦克风媒体时，浏览器会释放 WebRTC，并通过同一 HTTPS origin 建立受认证的 WSS PCM 通道。回退通道输入为 16 kHz PCM16、输出为 24 kHz PCM16，后续仍进入同一条 Pipecat 流水线。服务器确认收到第一帧输入后，正常 LAN 连接继续使用 WebRTC；只有未确认输入的连接才会自动回退。

## 取消与单会话约束

系统只允许一个活跃语音会话。每次助手回答都带一个单调递增的 `utterance_id`。检测到用户开始说话时，取消控制器会使旧 LLM、TTS、输出缓冲和未来数字人输出失效；迟到的 token、PCM 或视频帧不会进入浏览器。该机制是“AI 说话时仍可插话”的前提。

当前已实现取消路径与阶段延迟事件，但真实设备上的可听首音和插话 p95 仍未完成最终验收。组件基准不能替代浏览器端到端结果，详见[语音基线记录](../benchmarks/voice-baseline.md)。

## 记忆与本地知识库

对话完成后，系统从已持久化的用户原话中保守生成候选记忆。候选先处于 `pending`，只有管理员在浏览器中批准后才会作为受限上下文进入后续会话。拒绝和删除的候选不会进入模型提示词。

知识库是独立的数据通道。系统只读取 `data/knowledge/inbox/` 内支持的普通文件，使用本地 BGE-M3 生成检索结果。文档片段被明确视为不可信数据，不能改变系统规则或获得工具权限。记忆面向用户事实和偏好；知识库面向可替换的文档，两者使用同一 SQLite 数据库中的独立记录。

## 工具与联网搜索

模型请求工具不等于工具立刻执行。ToolGateway 先按策略分类：受限目录中的 `pwd` 与受限形式的 `ls` 可自动完成；受支持的 `touch`、`mkdir`、`cat` 需要一次性浏览器批准；shell、解释器、权限更改、删除、关机和其他危险命令被硬拒绝。批准记录绑定原始参数、可过期且只能消费一次。

联网搜索默认关闭。启用后，Agent 只能请求本机回环地址的 SearXNG JSON 服务，应用保留有限结果，且不直接抓取结果网页。搜索服务需要访问互联网，但语音、记忆、RAG 和终端工具不依赖它。

## 为音色克隆和数字人保留的接口

`OutputMux` 在 `direct_audio` 与未来 `avatar_av` 输出模式间保持互斥。现阶段浏览器直接播放 TTS 音频；后续数字人适配器可以消费带时间戳的 PCM、音量、口型和受限情绪事件，并在 `avatar_av` 模式拥有最终音频，避免重音或口型漂移。音色克隆、语气、情绪和真实数字人后端尚未接入；它们是后续里程碑，不应被当作已部署功能。
