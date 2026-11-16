# 首次在本机完成一次语音对话

本教程带你在服务主机本机完成首次登录和浏览器语音会话。完成后，浏览器会显示实时字幕和状态，并能从麦克风向本机模型发送语音。请先阅读 [配置参考](../reference/configuration.md)，确认机器具备 NVIDIA GPU、Docker、Python 3.12、Node.js 与 `uv`。

本教程假定仓库位于 `/home/rrrrio/custom_chat/arch`，使用默认域名 `arch.local` 和端口 `8443`。

## 1. 初始化依赖与本地配置

在仓库根目录执行：

    cp .env.example .env
    make bootstrap
    make frontend-build
    make compose-config

前三项分别创建项目依赖、生成浏览器静态文件并验证 Compose 配置。它们不删除 `data/` 中已有的模型或用户数据。

## 2. 准备固定的本地模型

首次下载需要网络。依次执行：

    make asr-prepare
    make llm-prepare
    make llamacpp-prepare
    make tts-prepare
    make rag-prepare

这些命令把语音识别、Qwen3 GGUF、llama.cpp、Kokoro 中文 TTS 和 BGE-M3 放到 `data/models/` 或 `data/runtime/`。运行时会检查固定路径；缺少任一语音模型时，建立会话会失败并列出缺失文件。

## 3. 初始化唯一管理员密码

执行以下交互式命令，并输入相同密码两次：

    uv run --directory backend python scripts/create_admin.py

密码至少需要 12 个字符。命令只会在 `data/auth/admin.json` 写入 Argon2id 哈希，不会回显密码。若需要轮换现有密码，使用 `--replace`。不要把密码放入 `.env`、命令行参数、脚本、Git 或日志。

## 4. 启动后端与 HTTPS 网关

在第一个终端启动后端：

    cd /home/rrrrio/custom_chat/arch/backend
    uv run uvicorn arch.main:app --host 127.0.0.1 --port 8000

在第二个终端启动网关：

    cd /home/rrrrio/custom_chat/arch
    make gateway-up

确认后端可用：

    curl http://127.0.0.1:8000/api/health

预期输出包含：

    {"status":"ok","environment":"development"}

## 5. 让本机浏览器解析域名并信任证书

为避免访问进入公司或系统代理，在本机 hosts 文件添加一行：

    127.0.0.1 arch.local

系统代理的绕过列表还必须包含 `arch.local`。Caddy 首次运行会生成本地 CA；导出其公有根证书：

    docker cp arch-caddy-1:/data/caddy/pki/authorities/local/root.crt /tmp/arch-root.crt

在浏览器的“证书颁发机构”中导入 `/tmp/arch-root.crt`，并只勾选“信任该 CA 以标识网站”。不要复制 `deploy/caddy-data/` 或其中的私钥。

## 6. 登录并开始说话

打开：

    https://arch.local:8443

使用第 3 步设置的密码登录。页面首先显示深色的实时语音舱：在声场核心下方选择物理麦克风，然后点击连接。不要选择“立体声混音”、系统回环或扬声器监听设备；使用扬声器时优先佩戴耳机。浏览器请求麦克风权限时选择允许。成功后，声场状态会从“等待连接”变为“可以说话”；说话后可看到用户字幕、助手字幕和阶段延迟。桌面左侧导航在鼠标移入或键盘焦点进入时展开；窄屏时它保持为横向导航。可从中打开记忆、知识库、审批、声音、主机 Agent 和数字人管理区，切换这些区域不会断开当前语音会话。

## 检查点

服务可用时，以下命令应返回 `HTTP 200`：

    curl --cacert /tmp/arch-root.crt \
      --resolve arch.local:8443:127.0.0.1 \
      https://arch.local:8443/api/health

如果页面显示“管理员密码尚未初始化”，重新执行第 3 步。若显示 `PR_END_OF_FILE_ERROR`，确认 hosts 与代理绕过项已设置；这通常表示浏览器将本地域名送进了上游代理，而不是证书文件本身失效。

下一步请阅读[让局域网设备访问](../how-to/lan-access.md)、[管理本地知识库](../how-to/manage-knowledge.md)或[启用联网搜索](../how-to/enable-web-search.md)。
