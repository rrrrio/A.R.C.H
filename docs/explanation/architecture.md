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

当前输入与输出分别为 16 kHz 和 24 kHz。ASR 使用本地 Faster-Whisper；会话创建时启动受后端拥有的 loopback llama.cpp；未选择声音参考时 TTS 使用 Kokoro 中文声线，并经本地 Misaki v1.1 生成中文音素。选择有效 profile 后使用隔离 Qwen worker。Qwen3 请求与 llama.cpp 服务均关闭 reasoning，避免推理内容占满实时工具调用的输出预算。

当 WebRTC 信令成功但服务器没有收到麦克风媒体时，浏览器会释放 WebRTC，并通过同一 HTTPS origin 建立受认证的 WSS PCM 通道。回退通道输入为 16 kHz PCM16、输出为 24 kHz PCM16，后续仍进入同一条 Pipecat 流水线。两条采集路径都会请求浏览器回声消除、降噪和自动增益，并静音本地麦克风监控，防止扬声器反馈被误判为插话；这不影响服务器语音输出。它不能修复被操作者选为输入的系统回环设备。服务器确认收到第一帧输入后，正常 LAN 连接继续使用 WebRTC；只有未确认输入的连接才会自动回退。

## 取消与单会话约束

系统只允许一个活跃语音会话。每次助手回答都带一个单调递增的 `utterance_id`。检测到用户开始说话时，系统在 VAD 后立即生成下游中断帧，使旧 LLM、TTS、输出缓冲和未来数字人输出失效；迟到的 token、PCM 或视频帧不会进入浏览器。输出复用器同时冻结当时所有 Pipecat TTS `context_id`，因此中断之后迟到的旧 TTS start、PCM 或 stop 帧不能重新激活旧回答。WebRTC 主路径会在中断帧到达输出传输前清除 SmallWebRTC 已排队、尚未发送的 PCM；浏览器同时从音频元素解绑旧远端 `MediaStream`，仅在下一轮 `speaking` 时重新绑定，避免解除静音后播放浏览器接收缓冲中的旧音频。WebSocket PCM 回退路径把字幕、说话状态、`audio_started(id)` 和 `audio_cancelled(id)` 放进同一条有序输出队列，并把后续 PCM 包与回答 ID 绑定。用户插话后，浏览器立即撤销当前 ID、清空已调度 PCM，并等待替换回答的助手字幕 `start`；只有在该可见新轮次开始后收到的、未撤销 ID 的 PCM 才播放。迟到的旧控制帧或二进制帧不能重新进入新回答的播放队列。该机制是“AI 说话时仍可插话”的前提。

当前已实现取消路径与阶段延迟事件，但真实设备上的可听首音和插话 p95 仍未完成最终验收。组件基准不能替代浏览器端到端结果，详见[语音基线记录](../benchmarks/voice-baseline.md)。

## 记忆与本地知识库

对话完成后，系统从已持久化的用户原话中保守生成候选记忆。候选先处于 `pending`，只有管理员在浏览器中批准后才会作为受限上下文进入后续会话。候选成功写入后，后端只向当前已认证媒体通道发送不含内容的刷新通知；浏览器仍通过既有受登录与 CSRF 保护的 API 读取其内容。拒绝和删除的候选不会进入模型提示词。具体体验步骤见[管理审批记忆](../how-to/manage-memory.md)。

知识库是独立的数据通道。系统只读取 `data/knowledge/inbox/` 内支持的普通文件，使用本地 BGE-M3 生成检索结果。文档片段被明确视为不可信数据，不能改变系统规则或获得工具权限。记忆面向用户事实和偏好；知识库面向可替换的文档，两者使用同一 SQLite 数据库中的独立记录。

## 工具与联网搜索

模型请求工具不等于工具立刻执行。ToolGateway 先按策略分类：受限目录中的 `pwd` 与受限形式的 `ls` 可自动完成；受支持的 `touch`、`mkdir`、`cat` 需要一次性浏览器批准；shell、解释器、权限更改、删除、关机和其他危险命令被硬拒绝。批准记录绑定原始参数、可过期且只能消费一次。

联网搜索默认关闭。启用后，Agent 只能请求本机回环地址的 SearXNG JSON 服务，应用保留有限结果，且不直接抓取结果网页。搜索服务需要访问互联网，但语音、记忆、RAG 和终端工具不依赖它。

## 为音色克隆和数字人保留的接口

`OutputMux` 在 `direct_audio` 与未来 `avatar_av` 输出模式间保持互斥。浏览器直接播放 Kokoro 或已选择 profile 的 Qwen 24 kHz PCM。Qwen worker 只在会话启动时有有效选择才进入隔离环境，通过 Unix socket 接收不含路径的 profile ID；Qwen 适配器按终止标点或 120 字符边界依次合成，并在每个句子的 PCM 完成后立即输出。缺少制品、启动失败或格式错误会使会话失败，绝不替换为 Kokoro。Pipecat 取消正在合成的 Qwen 请求时会终止对应隔离 worker，防止旧生成继续占用资源或重新输出。一次本地合成 WAV smoke 已观察到 worker 输出 PCM，但尚未证明浏览器可听音色、首音或插话指标。

Agent Core 可以为下一段 TTS 输出提供 `neutral`、`warm`、`calm` 或 `urgent` 中的一个受限标签。媒体边界会把其他值归一为 `neutral`，并在用户插话时重置为 `neutral`。该标签只传入 `OutputMux.begin`，供未来数字人或兼容 TTS 适配器使用；它不是 Qwen Base 的任意风格参数，当前直接浏览器音频也不会向客户端暴露表达事件。后续数字人适配器可以消费带时间戳的 PCM、音量、口型和该受限标签，并在 `avatar_av` 模式拥有最终音频，避免重音或口型漂移。真实数字人后端、浏览器表达渲染和设备级验收仍未完成。

浏览器数字人预览是独立于 `avatar_av` 的第一条实现路径。它使用浏览器本地选择的 VRM 文件和 WebGL 渲染；模型不经过 API、不写入 `data/`，也不进入备份。WebRTC 路径从已接收的音频统计读取能量，WebSocket PCM 路径从收到的 PCM 计算能量，两者都不会增加音频 destination 或第二路播放。该能量驱动 fallback 或具有 `aa` 元音表情的 VRM 的基础嘴部开合；当前没有服务器视频轨、浏览器表达事件或可接受的设备帧率/音画同步证据。直接音频仍是唯一输出所有者。
