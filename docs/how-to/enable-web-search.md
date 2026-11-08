# 启用或关闭联网搜索

本指南为本地语音会话启用受限网页搜索。完成标准是 SearXNG 只在服务主机回环地址运行，应用在用户请求搜索时只获得最多五条标题、URL 和摘要，而不会直接访问网页正文。

## 启动本地 SearXNG

在仓库根目录执行：

    make search-up

首次运行会创建受 Git 忽略的 `data/runtime/searxng.env`。它包含 SearXNG 运行时密钥，权限必须为 `0600`。该文件缺失、格式错误或密钥太短时，容器会拒绝启动。不要复制、提交或在日志中打印此文件。

不要运行会渲染 Compose 环境变量的 `docker compose config`；它可能在终端输出中显示运行时密钥。仓库的 `make compose-config` 使用 `--quiet`，只验证配置而不打印渲染结果。

确认容器状态：

    docker compose -f deploy/compose.yaml ps searxng
    curl http://127.0.0.1:8080/search?q=local+voice+agent&format=json

服务只绑定 `127.0.0.1:8080`。不要添加 LAN 端口映射，也不要把该地址直接交给浏览器客户端。

## 让应用使用搜索服务

编辑 `.env`，设置：

    LVA_SEARCH_ENABLED=true
    LVA_SEARXNG_ENDPOINT=http://127.0.0.1:8080

重启运行在 `127.0.0.1:8000` 的后端，使其重新读取配置。应用拒绝非回环、带路径、查询、用户名或密码的搜索端点。

在语音会话中明确要求搜索。模型可请求 `web_search`；网关会限制响应体为 64 KiB，最多保留五条有效结果。搜索引擎不可达时，当前会话应报告工具暂时不可用，而不应把异常当作普通回答文本。

## 停止服务

停止搜索容器：

    make search-down

关闭功能时，把 `.env` 改回 `LVA_SEARCH_ENABLED=false` 并重启后端。保留密钥文件不会启用搜索；只有设置变量并运行容器后，应用才会创建搜索客户端。

## 排查

使用以下命令查看最近日志：

    make search-logs

若 `make search-up` 提示运行时密钥无效，不要手工覆盖文件。先检查其是否被意外编辑或权限是否不是 `0600`；仅在确定没有要保留的部署状态后再处理该受忽略文件。搜索不能替代本地 RAG；个人资料应放在本地知识库，而不是发送到互联网查询。
