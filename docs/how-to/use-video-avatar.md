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

在准备窗口前运行 `make musetalk-dry-run` 查看固定源码 revision、目标目录和资源前置条件。生产服务使用隔离的 `data/runtime/musetalk` Python 3.10/CUDA runtime；源码 checkout 固定在 `data/runtime/musetalk/source`，模型目录固定为 `ARCH_AVATAR_MODEL_ROOT`。先在维护窗口运行 `make avatar-musetalk-prepare` 生成固定视频缓存，再运行 `make avatar-musetalk` 启动本机 worker，便于日志和 GPU 诊断。

查看将要安装的服务：

    make systemd-dry-run

安装后，avatar 服务可以单独启用：

    make systemd-install APPLY=1
    systemctl --user daemon-reload
    systemctl --user enable --now arch-avatar.service

后端和网关仍按[运行操作](../OPERATIONS.md)启动。浏览器登录后，在“真人视频”区域显式启用视频；关闭开关会释放视频 WebRTC 会话，语音会话继续运行。

## 当前状态和限制

视频 WebRTC、同源鉴权、音频静音边界、TTS PCM observer、取消旧帧和 VRM 备用模式已经接入自动化测试。独立 worker 具备 preview transport smoke 路径。

worker 会为每个有效播报轮次将 A.R.C.H. 的 24 kHz PCM 连续转换为 MuseTalk 所需 16 kHz 输入；取消后的旧轮次不会继续进入 renderer，任意 IPC 分块不会改变输出采样时间线。MuseTalk worker 现已加载固定 UNet、VAE 与 Whisper 并通过官方扩散/混合路径生成 JPEG 帧，不会落盘临时 WAV 或 MP4。

这一版的真实 renderer 在 `end` 后处理一个完整 utterance，而不是逐 PCM chunk 实时出帧；这是为了保留官方 Whisper 上下文并确保取消可丢弃整批旧帧。每个固定 MP4 都必须在维护窗口生成自己的 face crop、latent、mask 缓存。缓存路径由 MP4 SHA-256、MuseTalk revision、FPS 和裁切参数共同寻址，位于 `ARCH_AVATAR_CACHE_DIR/<cache-key>`。没有完整缓存时 worker 会拒绝启动，既有语音不会被阻断。

RTX 4090 的首帧时间、稳定 FPS、显存峰值、音视频延迟和浏览器设备表现尚未验证。真实模型制品已准备，但当前共享 GPU 的运行中语音服务没有被停止；完成设备验收后必须记录上述测量值和插话停止时间。

预处理缓存必须由固定 MP4 的内容、MuseTalk revision 和人脸裁切参数共同确定。不要手动将一个视频的 latent、mask 或 face crop 目录复制给另一个视频；worker 会用 manifest 拒绝不匹配缓存。

## 视觉设计

页面参考下载原型的黑底竖屏人物布局：桌面端视频靠右、左边缘渐隐，视频层不拦截聊天和语音控件；移动端视频缩小到下方区域。视频元素始终静音，所有音频都来自 A.R.C.H. 的既有语音连接。
