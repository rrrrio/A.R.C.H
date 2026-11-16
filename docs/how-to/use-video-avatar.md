# 使用真人视频数字人

真人视频数字人使用固定真人 MP4 作为姿态和人物来源，再由独立 GPU worker 根据 A.R.C.H. 的 TTS PCM 生成口型视频。浏览器视频连接只传视频；声音仍由现有语音 WebRTC 或 WSS 连接播放，因此不会出现两路声音。

## 准备素材

首版不提供网页上传。管理员将自己有权使用的素材放入受控目录：

    mkdir -p data/avatars
    cp /path/to/idle.mp4 data/avatars/idle.mp4
    make avatar-check

当前下载原型中的 `idle.mp4` 是 704×896 的竖屏参考视频，服务会在启动前检查它是否包含可解码的视频帧。`listening.mp4` 和 `speaking.mp4` 可以用于前端 mock 状态，但不能替代实时生成的视频；预录的 speaking 视频会与实际回答内容不同步。

## 配置

在 `.env` 中启用功能：

    ARCH_VIDEO_AVATAR_ENABLED=true
    ARCH_AVATAR_WORKER_BACKEND=musetalk

`musetalk` 是正式渲染后端；`preview` 只循环 `idle.mp4`，用于验证 WebRTC、网页构图和连接关闭，不会对口型。当前仓库不会把下载包中的 `wav2lip256.pth` 自动加载为正式模型。

## GPU 容量

真人视频不是轻量网页效果：MuseTalk 模型在加载阶段需要数 GiB 的 CUDA 显存，并会与 CUDA ASR、全 GPU LLM 和 Qwen 声音克隆竞争同一张卡。当前主机实测在这三个语音组件都常驻时，MuseTalk worker 可能在加载 UNet 时 OOM；不要依赖 `ARCH_AVATAR_VRAM_BUDGET_MB` 解决这个问题，它目前只记录运维预算，不实施 GPU 配额或抢占。

启用真人视频前，应在维护窗口确认模型能够启动并在语音会话下保留稳定余量。若失败，必须选择释放部分 LLM/ASR 显存、停用 Qwen 克隆音色、将一个组件迁移到另一张 GPU，或保持视频关闭。降低网页 FPS、放大低分辨率素材或反复重启 worker 都不能创造 CUDA 显存。

修改 `.env` 后必须重启当前 backend，才能让已运行的 Python 进程读取视频开关和新增的 `/api/avatar/*` 路由。使用用户级 unit 的部署可执行：

    systemctl --user restart arch-backend.service
    systemctl --user enable --now arch-avatar.service

登录后“真人视频”显示“服务不可用：MuseTalk renderer is not running.”时，页面已检测到私有 worker socket 缺失，不会发送一次必然失败的 WebRTC offer。先确认 backend 已从当前 checkout 重启；未认证的 `GET /api/avatar/status` 应返回 `401`，而不是 `404`。然后确认 `systemctl --user status arch-avatar.service` 为 `active`，日志中包含 `avatar worker ready`。这些检查只证明路由和本地 worker 已准备，尚不证明浏览器已收到视频帧。

在准备窗口前运行 `make musetalk-dry-run` 查看固定源码 revision、目标目录和资源前置条件。生产服务使用隔离的 `data/runtime/musetalk` Python 3.10/CUDA runtime；源码 checkout 固定在 `data/runtime/musetalk/source`，模型目录固定为 `ARCH_AVATAR_MODEL_ROOT`。先在维护窗口运行 `make avatar-musetalk-prepare` 生成固定视频缓存，再运行 `make avatar-musetalk` 启动本机 worker，便于日志和 GPU 诊断。

可在不修改 `.env` 或启动常驻服务的条件下运行服务端 smoke：

    make avatar-musetalk-smoke

该命令临时启动 worker，经真实 Unix socket（含超过默认单行 reader 限制的两秒 24 kHz PCM）、WebRTC 信令和本地 recvonly peer 解码三帧视频，并断言帧不会突发到达后自行清理。它不验证真实浏览器或设备表现。

当 GPU 没有空间启动第二个 renderer 时，可在没有浏览器视频会话的维护窗口连接已启用的 worker：

    uv run --no-sync --directory backend python scripts/smoke_musetalk_avatar.py \
      --existing-worker --socket data/runtime/avatar/avatar.sock

该模式只关闭自身建立的 Unix socket 和 WebRTC 会话，不会重启常驻 worker；不要与正在使用真人视频的浏览器会话并发运行。

要验证已经启用的服务在实际浏览器中完成登录、视频解码和关闭，请提供当前 HTTPS 地址和管理员密码，并运行：

    ARCH_AVATAR_BROWSER_URL=https://arch.local:8443 \\
    ARCH_AVATAR_BROWSER_PASSWORD='管理员密码' \\
    make avatar-browser-smoke

该命令不会启动或重启后端、网关或 worker，也不会修改 `.env`。它使用一次性 headless Chrome profile、固定本地麦克风 WAV 和已有语音会话驱动 renderer，要求视频元素解码至少三帧，再显式关闭视频和语音会话。它要求服务已在启动时启用 `ARCH_VIDEO_AVATAR_ENABLED=true`，且 `arch-avatar.service` 已运行；若状态显示服务不可用，应先按本文的维护窗口步骤修复运行条件。headless Chrome 证明浏览器 WebRTC 和视频解码，不替代远端设备上的画面、音画同步或听感验收。

不依赖已部署服务的主机验证可运行：

    make avatar-owned-browser-smoke

它临时启动固定 revision 的 MuseTalk worker、最小认证/avatar API 和同源 HTTPS Caddy，再由 headless Chrome 登录并开启视频。harness 在浏览器完成真实 offer 后通过同一服务接口注入 1 秒 24 kHz PCM，要求 `<video>` 解码至少三帧后关闭会话，并删除 worker、socket、临时凭据、Chrome profile 和 Caddy 容器。此命令验证真实 renderer、浏览器 WebRTC 与视频元素解码，但刻意不加载完整语音模型；已部署服务上的端到端 TTS observer 仍应使用上一条 `avatar-browser-smoke` 验证。

当前主机已成功运行拥有式 smoke：浏览器报告 3 个解码帧回调，视频尺寸为 704×896。该结果证明本机 Chrome 可解码真实 renderer 输出；它不证明远端设备画面、持续帧率、音画同步、插话停止时间或听感。

查看将要安装的服务：

    make systemd-dry-run

安装后，avatar 服务可以单独启用：

    make systemd-install APPLY=1
    systemctl --user daemon-reload
    systemctl --user enable --now arch-avatar.service

后端和网关仍按[运行操作](../OPERATIONS.md)启动。浏览器登录后，在“真人视频”区域显式启用视频；关闭开关会释放视频 WebRTC 会话，语音会话继续运行。

## 当前状态和限制

视频 WebRTC、同源鉴权、音频静音边界、TTS PCM observer、取消旧帧和 VRM 备用模式已经接入自动化测试。独立 worker 具备 preview transport smoke 路径。

worker 会为每个有效播报轮次将 A.R.C.H. 的 24 kHz PCM 连续转换为 MuseTalk 所需 16 kHz 输入；后端会把较大的 TTS PCM 帧拆成受限的 Unix socket JSON 记录，避免 Base64 载荷超过 reader 行长度。反向的 JPEG Base64 帧也使用相同的 256 KiB reader 边界，避免高质量帧在 backend 侧被截断；取消后的旧轮次不会继续进入 renderer，任意 IPC 分块不会改变输出采样时间线。GPU 一次生成多帧时，视频 track 仍按 `ARCH_AVATAR_FPS` 均匀发送，避免 browser 收到突发帧后停顿。MuseTalk worker 现已加载固定 UNet、VAE 与 Whisper，并以 JPEG quality 95 通过本地 IPC 传递帧，不会落盘临时 WAV 或 MP4。

真实 renderer 在积累至少 8 帧的稳定音频后就启动 GPU batch；为保留 Whisper 右侧上下文，最近 2 帧会暂存，并在后续音频或 `end` 时 flush。后续 batch 仅带少量左侧上下文和未发布 PCM，不会从 utterance 起点重算。取消或连接关闭会 fence 已在运行的 batch，因此旧回答不会将 JPEG 帧发布给下一轮。每个固定 MP4 都必须在维护窗口生成自己的 face crop、latent、mask 缓存。缓存路径由 MP4 SHA-256、MuseTalk revision、FPS 和裁切参数共同寻址，位于 `ARCH_AVATAR_CACHE_DIR/<cache-key>`。没有完整缓存时 worker 会拒绝启动，既有语音不会被阻断。

当前共享 GPU 主机的私有 socket smoke 对 1 秒 24 kHz PCM 在尚未收到 `end` 时返回首个 JPEG，耗时约 0.97 秒，随后共返回 25 帧；两个连续 1 秒 batch 返回 23、25 和 2 帧，总计 50 帧。本地 `aiortc` 对端也已完成 SDP 协商并从真实视频 track 解码 704x896 帧。这些只证明服务端 worker、WebRTC 与 GPU renderer 的主机路径，不证明浏览器视频、持续 FPS、音画延迟、显存峰值或设备体验；完成设备验收后必须记录这些测量值和插话停止时间。

预处理缓存必须由固定 MP4 的内容、MuseTalk revision 和人脸裁切参数共同确定。不要手动将一个视频的 latent、mask 或 face crop 目录复制给另一个视频；worker 会用 manifest 拒绝不匹配缓存。

## 视觉设计

点击“启用视频”只建立视频 WebRTC 连接。renderer 在已有语音回答产生 TTS PCM 后才会生成首帧；页面在此之前显示“已连接，等待首帧”，不会展示黑色 video layer。收到首个解码帧后才显示视频。

页面参考下载原型的黑底竖屏人物布局：桌面端视频靠右、左边缘渐隐，视频层通过页面根部 layer 放在所有聊天和语音控件之后，不会遮黑或拦截控件；移动端视频缩小到下方区域。视频元素始终静音，所有音频都来自 A.R.C.H. 的既有语音连接。清晰度仍受固定 MP4 的原始分辨率、人物脸部面积和浏览器 WebRTC 编码约束；不要把低码率或低分辨率素材放大后当作高清输入。
