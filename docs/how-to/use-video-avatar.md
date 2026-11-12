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

查看将要安装的服务：

    make systemd-dry-run

安装后，avatar 服务可以单独启用：

    make systemd-install APPLY=1
    systemctl --user daemon-reload
    systemctl --user enable --now arch-avatar.service

后端和网关仍按[运行操作](../OPERATIONS.md)启动。浏览器登录后，在“真人视频”区域显式启用视频；关闭开关会释放视频 WebRTC 会话，语音会话继续运行。

## 当前状态和限制

视频 WebRTC、同源鉴权、音频静音边界、TTS PCM observer、取消旧帧和 VRM 备用模式已经接入自动化测试。独立 worker 具备 preview transport smoke 路径。

worker 会为每个有效播报轮次将 A.R.C.H. 的 24 kHz PCM 连续转换为 MuseTalk 所需 16 kHz 输入；取消后的旧轮次不会继续进入 renderer，任意 IPC 分块不会改变输出采样时间线。这不是可用的口型 renderer：权重、固定视频预处理缓存和 GPU 推理仍未准备。

MuseTalk 1.5 的权重、预处理缓存和 RTX 4090 实时 FPS 尚未在本机完成准备和设备验证。没有准备好 GPU worker 时，页面会报告视频不可用，现有语音不会被阻断。完成真实模型准备后，必须重新记录首帧时间、稳定 FPS、显存峰值、音视频延迟和插话停止时间。

## 视觉设计

页面参考下载原型的黑底竖屏人物布局：桌面端视频靠右、左边缘渐隐，视频层不拦截聊天和语音控件；移动端视频缩小到下方区域。视频元素始终静音，所有音频都来自 A.R.C.H. 的既有语音连接。
