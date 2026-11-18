# 使用真人视频数字人

> 当前正式路径是单 peer `arch-avatar-media.service`。旧 `arch-avatar.service`、Unix-socket JPEG bridge、`avatar_runtime/` 和本页中提到它们的旧命令均已在 2026-11-18 退役并删除，不能安装、启用或排障使用。下文保留的旧段落仅作历史迁移记录；以“新单 peer runtime”的步骤为准。

旧路径把视频与普通语音放在不同 WebRTC peer，不能满足音画同步要求。用户拥有的 `data/avatars/`、模型、cache 和源码 checkout 未被删除。

## 准备素材

首版不提供网页上传。管理员将自己有权使用的素材放入受控目录：

    mkdir -p data/avatars
    cp /path/to/idle.mp4 data/avatars/idle.mp4
    make avatar-media-wav2lip-prepare

当前下载原型中的 `idle.mp4` 是 704×896、16fps 的竖屏参考视频，服务会在启动前检查它是否包含可解码的视频帧。Wav2Lip 必须使用由该视频生成的同一人物缓存（本机默认 `wav2lip_idle_reference`，包含 `full_imgs/`、`face_imgs/`、`coords.pkl`）和 `wav2lip.pth` 生成说话画面。静音和模型生成期间会直接循环原始 `idle.mp4`，并按源视频时间轴映射到视频轨的 25fps，不会把 16fps 源素材按 25fps 倍速播放；`listening.mp4` 和 `speaking.mp4` 只是预录状态素材，不能替代实时生成的视频，否则会与实际回答内容不同步。

## 配置

在 `.env` 中启用功能：

    ARCH_VIDEO_AVATAR_ENABLED=true
    ARCH_AVATAR_MEDIA_BACKEND=wav2lip

当前正式渲染后端为 Wav2Lip：

    ARCH_AVATAR_MEDIA_BACKEND=wav2lip

它使用 loopback media runtime 的单一 audio/video peer，不启动下载方案的 HTTP、TTS 或 WebRTC 服务。每个完成的 TTS 回答准备为一段同步媒体；插话会 fence 当前片段，旧帧不会进入下一轮。

Wav2Lip 必须由运维方在以下受控路径准备来源、checkpoint 和同一人物的预处理缓存。运行时会按参考工程的正向后反向帧序遍历缓存，避免循环处从末帧跳回首帧：

    data/runtime/livetalking/source
    data/runtime/livetalking/wav2lip.pth
    data/runtime/livetalking/avatars/wav2lip256_avatar1/

缓存目录必须包含 `coords.pkl`、`full_imgs/` 和 `face_imgs/`。模型、缓存和真人素材都不随仓库再分发；部署前须确认人物肖像和 checkpoint 的使用权。路径可用对应的 `ARCH_AVATAR_WAV2LIP_*` 配置覆盖，详见[配置参考](../reference/configuration.md)。

## GPU 容量

真人视频不是轻量网页效果：MuseTalk 模型在加载阶段需要数 GiB 的 CUDA 显存，并会与 CUDA ASR、全 GPU LLM 和 Qwen 声音克隆竞争同一张卡。当前主机实测在这三个语音组件都常驻时，MuseTalk worker 可能在加载 UNet 时 OOM；不要依赖 `ARCH_AVATAR_VRAM_BUDGET_MB` 解决这个问题，它目前只记录运维预算，不实施 GPU 配额或抢占。

启用真人视频前，应在维护窗口确认模型能够启动并在语音会话下保留稳定余量。若失败，必须选择释放部分 LLM/ASR 显存、停用 Qwen 克隆音色、将一个组件迁移到另一张 GPU，或保持视频关闭。降低网页 FPS、放大低分辨率素材或反复重启 worker 都不能创造 CUDA 显存。

修改 `.env` 后必须重启当前 backend，才能让已运行的 Python 进程读取视频开关和新增的 `/api/avatar/*` 路由。`arch-avatar-media.service` 保存单 peer 的进程内会话；部署或重启 backend 时必须同时重启它，避免旧认证会话继续占用唯一的视频 peer。使用用户级 unit 的部署可执行：

    systemctl --user restart arch-backend.service arch-avatar-media.service
    systemctl --user enable --now arch-avatar-media.service

登录后“真人视频”显示运行时不可用时，页面不会发送一次必然失败的 WebRTC offer。先确认 backend 已从当前 checkout 重启；未认证的 `GET /api/avatar/status` 应返回 `401`，而不是 `404`。然后确认 `systemctl --user status arch-avatar-media.service` 为 `active`，且 `curl --fail --silent http://127.0.0.1:8101/health` 返回 `{"status":"ready"}`。这些检查只证明路由和本地 runtime 已准备，尚不证明浏览器已收到媒体帧。

在准备窗口前运行 `make musetalk-dry-run` 查看固定源码 revision、目标目录和资源前置条件。生产服务使用隔离的 `data/runtime/musetalk` Python 3.10/CUDA runtime；源码 checkout 固定在 `data/runtime/musetalk/source`，模型目录固定为 `ARCH_AVATAR_MODEL_ROOT`。先在维护窗口运行 `make avatar-musetalk-prepare` 生成固定视频缓存，再运行 `make avatar-musetalk` 启动本机 worker，便于日志和 GPU 诊断。

可在不修改 `.env` 或启动常驻服务的条件下运行服务端 smoke：

    make avatar-musetalk-smoke

该命令临时启动 worker，经真实 Unix socket（含超过默认单行 reader 限制的两秒 24 kHz PCM）、WebRTC 信令和本地 recvonly peer 解码三帧视频，并断言帧不会突发到达后自行清理。它不验证真实浏览器或设备表现。

旧 worker 的 Unix-socket smoke 和旧浏览器 smoke 已退役。新单 peer runtime 的浏览器验证使用已安装且健康的 `arch-avatar-media.service`：

    make avatar-media-browser-smoke

该命令只创建临时认证凭据、同源 HTTPS Caddy 和 Chrome profile；浏览器的 offer 仍经过
`/api/avatar/*`，而完整 PCM 仅由临时 API 私下提交给 loopback runtime。它不会读取或输出
ticket secret，也不会重启 runtime、后端或旧 bridge。运行时只允许一个 peer，因此遇到正在
使用视频的会话会安全失败而不取代该会话。成功条件是同一个未静音的 `<video>` 元素拥有各一条
live audio/video track、至少 30 个视频帧回调的有效帧率在 22-28fps、没有连续短于 25ms 的
回调或超过 70ms 的停顿，且没有第二个未静音助手媒体元素。该临时 harness 不启动完整的语音
会话；普通助手音频交接由前端回归覆盖。它仍是主机浏览器 smoke，不替代目标设备的音画同步、
听感、插话或画质验收。

2026-11-17 的主机实测得到 1 条 audio track、30 个视频回调和 `704x896` 视频；有效帧率约
25fps，所有相邻回调均未超过 70ms。该结果只证明本机 Chrome 的传输与播放所有权，不应描述为
目标设备或主观听感验收。

该 smoke 也会显式启用“目标设备验收”，以覆盖浏览器到 runtime 的验收 DataChannel 协商；它不生成
观察 JSON，也不替代目标设备的五轮验收。

## 目标设备验收

主机 smoke 通过后，必须在实际目标设备完成 5 个独立媒体会话。每一轮记录到受忽略 JSON 的
固定字段只有：`audio_first_ms`、`video_first_ms`、`fps`、`cancellation_ms`、
`watermark_absent` 和 `clarity_acceptable`。每轮先显式启用“目标设备验收”，再启用视频并完成一次
回答和一次插话；结束后关闭视频，使下一轮重新建立独立媒体会话。浏览器在本地自动收集前四个数值，
不会上传或持久化它们。`audio_first_ms` 是同一浏览器会话中 audio track 首次 unmute 的单调计时，
`video_first_ms` 是首个已解码视频帧的单调计时；`fps` 是首 30 个视频帧回调窗口的有效帧率；
`cancellation_ms` 是设备发起插话到浏览器解码确认的首个 idle 视频帧。验收模式不会调用既有的
`/api/avatar/timing` 服务端日志端点。该确认
只在 runtime 已为音频和视频都发送 idle 帧后生效，因此不会把仍在缓冲的旧说话画面计为已取消。

验收数据流如下。DataChannel 只含固定的 `speaking`/`complete`/`idle` 事件名及 idle 视频 RTP 时间戳；
它不含会话 ID、用户名、文本、PCM、图像、SDP、ticket 或设备标识。只有收到本轮 `speaking` 标记后，浏览器才会把
用户插话作为取消测量起点，避免首次提问或空闲状态误记为取消；双轨自然回到 idle 时的 `complete` 会清除该条件。关闭验收模式会清空尚未导出的浏览器内存记录；关闭视频只会丢弃
尚未保存的当前轮次。

```text
[browser: explicit acceptance mode]
       | first audio/video callback, 30-frame cadence
       v
[local timing-only observation] <-- [runtime: speaking, then both tracks idle]
       |                                        |
       +----------- WebRTC data marker --------+
       |
       v
[manual watermark + clarity booleans] --> [downloaded 5-run JSON]
```

完成一轮后，在数字人区域确认无水印和清晰度合格并先保存本轮，再关闭视频以开始下一轮独立会话。
五轮完成时下载 JSON，将文件放入主机的 `data/benchmarks/results/avatar-device-observations.json`。
关闭当前视频只丢弃未保存的本轮数据；关闭验收模式会清空尚未导出的浏览器内存记录。浏览器不会自动替人
判断水印或主观清晰度。

```json
[
  {
    "audio_first_ms": 121,
    "video_first_ms": 138,
    "fps": 25.0,
    "cancellation_ms": 184,
    "watermark_absent": true,
    "clarity_acceptable": true
  }
]
```

样本文件必须恰好包含 5 个同样结构的对象。完成后生成只含 timing 与主观布尔结果的本地报告：

    make avatar-device-report AVATAR_DEVICE_INPUT=data/benchmarks/results/avatar-device-observations.json

报告写入受忽略的 `data/benchmarks/results/avatar-device-acceptance.json`，包含音画 decoded-start
skew、有效 FPS 和取消时延的中位数 p50、nearest-rank p95/max。2026-11-18 的真实目标设备报告包含 5 轮：
FPS p50 为 23.83、p95 为 25.21，取消 p50 为 20ms、p95 为 106ms，水印和清晰度检查全部通过。音画起始
偏差 p50 为 5ms，但首轮为 122ms（p95/max 122ms），超过原始单轮 100ms 目标；其余四轮为 2–12ms。因此
视频功能已经**设备验证且有五轮量化记录**，但不能表述为二十轮研究或“全部五轮都满足 100ms 偏差”。

查看将要安装的服务：

    make systemd-dry-run

安装后，avatar 服务可以单独启用：

    make systemd-install APPLY=1
    systemctl --user daemon-reload
    systemctl --user enable --now arch-avatar-media.service

后端和网关仍按[运行操作](../OPERATIONS.md)启动。浏览器登录后，在导航中打开“数字人”，在“真人视频”区域显式启用视频；关闭开关会释放视频 WebRTC 会话，语音会话继续运行。

语音传输因网络条件从 WebRTC 回退到同源 WebSocket 时，已启用的视频仍使用同一认证浏览器会话提交完整回答给单 peer 音视频 runtime；不需要重新开启视频。用户插话只会丢弃当前回答尚未播放的音视频，不会结束该视频 WebRTC 轨道；下一次完整回答应继续在同一会话中显示。没有完整回答播放或当前回答被取消时，视频轨持续发送原始 idle 帧，不会停在最后一个说话帧。每个完整回答会在保留单调 RTP 时间戳的同时重新建立本轮音频和视频发送节拍，空闲或取消后的下一轮不能把已过期帧突发发送成追赶口型。若页面显示视频已连接但持续没有首帧，应先按本页的 runtime 健康检查确认服务正常，再查看 `arch-avatar-media.service` journal 中是否出现该轮的 `runtime-submission` 时序事件。普通语音仍是 runtime 不可用或提交失败时的回退路径。

## 当前状态和限制

视频 WebRTC、同源鉴权、音频静音边界、TTS PCM observer、取消旧帧和 VRM 备用模式已经接入自动化测试。独立 worker 具备 preview transport smoke 路径。

worker 会为每个有效播报轮次将 A.R.C.H. 的 24 kHz PCM 连续转换为 MuseTalk 所需 16 kHz 输入；后端会把较大的 TTS PCM 帧拆成受限的 Unix socket JSON 记录，避免 Base64 载荷超过 reader 行长度。反向的 JPEG Base64 帧也使用相同的 256 KiB reader 边界，避免高质量帧在 backend 侧被截断；取消后的旧轮次不会继续进入 renderer，任意 IPC 分块不会改变输出采样时间线。GPU 一次生成多帧时，视频 track 仍按 `ARCH_AVATAR_FPS` 均匀发送，避免 browser 收到突发帧后停顿。MuseTalk worker 现已加载固定 UNet、VAE 与 Whisper，并以 JPEG quality 95 通过本地 IPC 传递帧，不会落盘临时 WAV 或 MP4。

真实 renderer 在积累至少 8 帧的稳定音频后就启动 GPU batch；为保留 Whisper 右侧上下文，最近 2 帧会暂存，并在后续音频或 `end` 时 flush。后续 batch 仅带少量左侧上下文和未发布 PCM，不会从 utterance 起点重算。取消或连接关闭会 fence 已在运行的 batch，因此旧回答不会将 JPEG 帧发布给下一轮。每个固定 MP4 都必须在维护窗口生成自己的 face crop、latent、mask 缓存。缓存路径由 MP4 SHA-256、MuseTalk revision、FPS 和裁切参数共同寻址，位于 `ARCH_AVATAR_CACHE_DIR/<cache-key>`。没有完整缓存时 worker 会拒绝启动，既有语音不会被阻断。

当前共享 GPU 主机的私有 socket smoke 对 1 秒 24 kHz PCM 在尚未收到 `end` 时返回首个 JPEG，耗时约 0.97 秒，随后共返回 25 帧；两个连续 1 秒 batch 返回 23、25 和 2 帧，总计 50 帧。本地 `aiortc` 对端也已完成 SDP 协商并从真实视频 track 解码 704x896 帧。这些只证明服务端 worker、WebRTC 与 GPU renderer 的主机路径，不证明浏览器视频、持续 FPS、音画延迟、显存峰值或设备体验；完成设备验收后必须记录这些测量值和插话停止时间。

Wav2Lip 成片模式已完成引擎直接检查：一秒 16 kHz PCM 生成 25 张 JPEG；其 worker 单元检查证明只有 `end` 才启动渲染，且该次播报完整输出按序发布。拥有式 Chrome smoke 已通过临时认证站点、真实 Wav2Lip worker 和 `<video>` 解码，得到 `576x768` 尺寸的 20 次帧回调，间隔为 33.2 到 67.0ms。桌面 CSS 将该 576x768 缓存的显示高度限制为最多 768px，避免在高分辨率屏幕额外上采样；Wav2Lip smoke 会在 1920x1080 Chrome 中断言该高度。可用 `make avatar-owned-wav2lip-browser-smoke` 复跑；它会构建前端并短暂启动本地 Caddy、worker 和 Chrome，完成后清理。Wav2Lip 运行会要求至少 20 次浏览器帧回调，并拒绝小于 15ms 的突发或大于 120ms 的停顿。该结果仍不测量目标设备上的整段生成耗时、持续帧率、音画同步或主观画质；在这些测量完成前，不应将该模式描述为已设备验证。

预处理缓存必须由固定 MP4 的内容、MuseTalk revision 和人脸裁切参数共同确定。不要手动将一个视频的 latent、mask 或 face crop 目录复制给另一个视频；worker 会用 manifest 拒绝不匹配缓存。

## 视觉设计

点击“启用视频”只建立视频 WebRTC 连接。renderer 在已有语音回答产生 TTS PCM 后才会生成首帧；页面在此之前显示“已连接，等待首帧”，不会展示黑色 video layer。收到首个解码帧后才显示视频。视频层必须挂在认证后工作区的 stacking context 内：它位于工作区背景之上、导航和控制内容之下，不能作为 `body` 的低层 sibling，否则工作区的隔离背景会遮住已解码视频。

页面参考下载原型的黑底竖屏人物布局：桌面端视频靠右、左边缘渐隐，视频层通过页面根部 layer 放在所有聊天和语音控件之后，不会遮黑或拦截控件；移动端视频缩小到下方区域。在新单 peer runtime 中，浏览器只在同时收到 audio/video track 后取消 `<video>` 静音并将普通助手音频交付权释放；关闭、失败或取消会恢复普通音频路径。清晰度仍受固定 MP4 的原始分辨率、人物脸部面积和浏览器 WebRTC 编码约束；不要把低码率或低分辨率素材放大后当作高清输入。
