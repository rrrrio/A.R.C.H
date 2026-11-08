# 让局域网设备访问

本指南将同一局域网内的浏览器接入已运行的服务。完成标准是客户端打开 `https://local-voice-agent.local:8443` 后能看到登录页，并能通过 HTTPS 健康检查。它适用于服务主机地址为 `192.168.10.126`、网段为 `192.168.10.0/23` 的当前部署。

## 前置条件

服务主机的后端已监听 `127.0.0.1:8000`，Caddy 已通过 `make gateway-up` 启动并监听 `8443`。不要把后端、Ollama、llama.cpp 或 SearXNG 端口发布到 LAN。

## 配置客户端名称解析

在局域网 DNS 中添加以下 A 记录：

    local-voice-agent.local.  A  192.168.10.126

测试或没有内部 DNS 时，在每台客户端 hosts 文件添加：

    192.168.10.126 local-voice-agent.local

Linux 与 macOS 使用 `/etc/hosts`；Windows 使用 `C:\\Windows\\System32\\drivers\\etc\\hosts`，需以管理员权限编辑。不要用裸 IP 访问。`https://192.168.10.126:8443` 没有匹配的 SNI 主机名和本地证书，Firefox 会在 TLS 握手阶段报错。

## 安装本地 CA

从服务主机复制公有根证书：

    /tmp/local-voice-agent-root.crt

该文件由以下命令导出：

    docker cp local-voice-agent-caddy-1:/data/caddy/pki/authorities/local/root.crt /tmp/local-voice-agent-root.crt

在每台客户端的浏览器或操作系统信任库中导入此文件，并只授权其“标识网站”。此 CA 只用于本部署，不应发送给不受信任的设备；绝不复制 Caddy 数据目录或私钥。

## 绕过上游代理

若设备设置了 HTTP/HTTPS/SOCKS 代理，在代理例外或 `NO_PROXY` 中加入：

    local-voice-agent.local

代理通常无法为本地私有 CA 建立端到端 TLS，会表现为 `PR_END_OF_FILE_ERROR`。完成设置后完全退出并重新打开浏览器。

## 放行网关端口

如果服务主机启用了 UFW，只放行该网段到 HTTPS 网关：

    sudo ufw allow proto tcp from 192.168.10.0/23 to any port 8443

用下列命令确认监听范围；预期 Caddy 为 `*:8443`，其余服务仅在 loopback：

    ss -ltn

## 验证与排查

在客户端打开 `https://local-voice-agent.local:8443`。首次访问应显示登录页，不应出现证书或代理错误。若可用命令行，可用服务主机 CA 验证：

    curl --cacert local-voice-agent-root.crt \
      --resolve local-voice-agent.local:8443:192.168.10.126 \
      https://local-voice-agent.local:8443/api/health

预期响应为 `{"status":"ok","environment":"development"}`。若无法连接，依次检查 DNS/hosts、客户端代理绕过、CA 信任、主机防火墙和 `make gateway-logs`。当前仅支持一个活跃语音会话；第二台设备可以打开登录页，但不应与现有会话并行通话。
