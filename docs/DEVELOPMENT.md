# 开发环境

本页面面向修改项目的开发者。部署与使用请从[文档导航](README.md)进入相应教程、操作指南、参考或架构说明。

## 初始化与质量检查

在仓库根目录执行：

    cp .env.example .env
    make bootstrap
    make lint
    make test
    make frontend-build
    make compose-config

`make bootstrap` 创建后端虚拟环境并按 `frontend/package-lock.json` 安装前端依赖；它不会覆盖 `.env` 或删除 `data/` 中的用户数据。当前全量 `ruff check .` 可能报告 `memory/migrations.py` 的既有长 SQL 行；修改新文件时至少应运行其范围内的 Ruff 检查，并在变更说明中区分既有失败与新增失败。

## 启动开发服务

后端只绑定回环地址：

    cd backend
    uv run uvicorn arch.main:app --host 127.0.0.1 --port 8000

前端开发服务器可单独运行：

    cd frontend
    npm run dev

浏览器 HTTPS 入口通过 Caddy 启动：

    make gateway-up

停止或查看网关日志：

    make gateway-down
    make gateway-logs

实际浏览器部署、证书、代理和 LAN 处理见[让局域网设备访问](how-to/lan-access.md)。

## 数据与迁移

所有可写的应用数据必须保留在 `data/` 内。Caddy 容器的受忽略状态位于 `deploy/caddy-data/` 和 `deploy/caddy-config/`，不要提交或复制其中的私钥。运行 SQLite migration：

    make migrate

准备和重建本地知识库：

    make rag-prepare
    make rag-index

详细目录限制和文件类型见[配置参考](reference/configuration.md)。不要将真实密码、CA 私钥、SearXNG 密钥、模型、数据库、日志或浏览器运行结果写入 Git。

## 文档同步要求

每次改变用户可见行为、部署步骤、网络/安全边界、配置参数、数据格式或验收状态时，必须在同一变更中更新相应 `docs/` 页面和 `execplan/arch.md`。选择一个与读者任务匹配的 Diataxis 类型：新手线性路径写入 `docs/tutorials/`，具体操作写入 `docs/how-to/`，事实与参数写入 `docs/reference/`，设计取舍写入 `docs/explanation/`。不要把多个类型重新堆回本页。

文档评审至少检查：命令是否存在、默认值是否与 `.env.example` 和代码一致、网络地址是否与 Caddy 和后端实际监听一致、未完成能力是否明确标注，以及新增或变更页面是否在 `docs/README.md` 中可找到。涉及多组件数据流时，使用简洁、证据可追溯的 ASCII 图。
