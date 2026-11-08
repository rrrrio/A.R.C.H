# 开发环境

当前工作区已完成 Milestone 0 的工程骨架和 Milestone 1 的组件基准。Milestone 2 已包含本机管理员认证基础，但浏览器语音会话与 LAN 验收尚未完成。日常开发不会下载或加载语音、语言、嵌入或数字人模型。

## 初始化

在仓库根目录执行：

    cp .env.example .env
    make bootstrap
    make lint
    make test
    make frontend-build
    make compose-config

`make bootstrap` 创建后端虚拟环境，并按 `frontend/package-lock.json` 安装前端依赖。它不会覆盖 `.env`，也不会删除 `data/` 内的用户数据。

## 健康检查

启动后端：

    cd backend
    uv run uvicorn local_voice_agent.main:app --host 127.0.0.1 --port 8000

在另一个终端请求：

    curl http://127.0.0.1:8000/api/health

预期结果：

    {"status":"ok","environment":"development"}

## 管理员密码

在首次通过 HTTPS 浏览器登录前，使用交互式 CLI 初始化本机唯一管理员。该命令通过终端读取密码两次，只会将 Argon2id 哈希写入受忽略的 `data/auth/admin.json`：

    uv run --directory backend python scripts/create_admin.py

如需轮换已存在的密码，显式传入 `--replace`：

    uv run --directory backend python scripts/create_admin.py --replace

不要把密码放入 `.env`、命令行参数、脚本、Git 或日志。认证 API 要求浏览器请求的 `Origin` 与 `LVA_PUBLIC_ORIGIN` 完全一致；该值必须是无路径的 HTTPS origin，例如 `https://local-voice-agent.local:8443`。

## 数据边界

应用只接受 `data/` 及其子目录作为可写目录。`LVA_DATA_DIR` 指向仓库外部路径时，后端会拒绝启动。模型、数据库、日志、知识文件、语音参考材料和管理员凭据都必须保存在 `data/` 下；Git 会忽略其内容，只保留目录占位文件。
