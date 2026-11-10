# 卸载

本页说明如何删除 A.R.C.H. 生成物，并默认保留用户数据。先停止后端终端进程、Caddy、SearXNG 和可选 Ollama，避免删除仍被服务使用的目录：

    make gateway-down
    make search-down
    docker compose -f deploy/compose.yaml --profile models stop ollama

若后端仍在运行，在启动它的终端按 `Ctrl-C`。

## 预览范围

先运行 dry-run：

    make uninstall-dry-run

当前命令只会列出以下生成物：

- `frontend/dist/`
- `deploy/caddy-data/`
- `deploy/caddy-config/`

它默认保留整个 `data/`，包括管理员凭据、数据库、知识资料、模型、备份、日志和运行时密钥。它也不会删除源码、`.env`、Docker 镜像、Docker 卷、全局 CA 信任设置或浏览器数据。

## 删除生成物

确认 dry-run 输出后，显式允许删除：

    make uninstall APPLY=1

该命令仅删除当时存在的列出路径；目标本身的符号链接会作为链接移除，不会跟随到链接目标。若目标父目录经符号链接解析到仓库外，命令会拒绝删除。

## 删除用户数据

只有确认已按[备份和恢复本地数据](how-to/backup-and-restore.md)保存了需要的归档后，才删除 `data/`：

    make uninstall APPLY=1 UNINSTALL_FLAGS=--remove-data

此操作会删除 `data/` 下的本地用户状态和模型，无法由本仓库恢复。它仍不会删除 Docker 镜像、Docker 卷、`.env`、源码或系统范围的证书信任设置。

当前卸载流程已经过单元测试和本机 dry-run 验证；尚未完成完整主机卸载或重装后的设备验收。
