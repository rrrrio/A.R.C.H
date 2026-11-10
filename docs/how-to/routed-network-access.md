# 通过已有穿透网络访问语音会话

本指南适用于浏览器已经能通过现有穿透网络访问服务主机所在 LAN 的情况，例如 NetBird 路由。浏览器仍使用原有地址 `https://arch.local:8443`，不需要 TURN 服务、第二个 URL 或手动选择传输模式。

## 前提

- 穿透网络能够到达服务主机所在网段，并能解析 `arch.local` 到服务主机的 LAN IP。
- 客户端已信任为该域名签发证书的本地 CA。
- 客户端未将该域名交给 HTTP/HTTPS 上游代理。代理绕过和名称解析的具体配置见[让局域网设备访问](lan-access.md)。
- 服务主机上的 HTTPS 网关和后端服务正在运行。

## 建立会话

1. 在穿透网络客户端打开 `https://arch.local:8443`。
2. 登录，选择麦克风后点击“连接语音”。
3. 等待页面显示“可以说话”，再说一句完整的话。

浏览器优先使用 WebRTC。若服务器在短暂等待内没有确认收到麦克风 PCM，页面会自动关闭该媒体会话，并通过同一 HTTPS 域名建立受认证的 WebSocket 音频通道。这个过程不要求重新登录、再次授权麦克风或手动切换模式。

## 验收

一次成功对话应同时满足：

- 页面显示用户转写；
- 页面显示助手转写；
- 客户端能听到助手语音。

当前部署已在经路由的浏览器上验证上述完整路径。系统仍只允许一个活跃语音会话。

## 排查

若页面可以登录但一直不能对话，先刷新页面，以获取当前前端版本，然后重新连接语音。服务主机上检查：

```bash
systemctl --user status arch-backend.service --no-pager
journalctl --user -u arch-backend.service -n 120 --no-pager
```

日志中的 `FastAPIWebsocketInputTransport`、`User started speaking` 和 `Transcription:` 依次表示回退通道已建立、服务器收到语音、识别已完成。若没有 WebSocket 或 WebRTC 建连记录，问题位于名称解析、证书、穿透路由或浏览器代理设置，而不是语音模型。
