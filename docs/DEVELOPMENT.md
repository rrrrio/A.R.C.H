# 开发环境

Milestone 0 建立可重复的本地工程。它只启动健康检查，不下载或加载语音、语言、嵌入或数字人模型。

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

## 数据边界

应用只接受 `data/` 及其子目录作为可写目录。`LVA_DATA_DIR` 指向仓库外部路径时，后端会拒绝启动。模型、数据库、日志、知识文件和语音参考材料都必须保存在 `data/` 下；Git 会忽略其内容，只保留目录占位文件。
