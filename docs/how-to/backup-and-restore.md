# 备份和恢复本地数据

本指南说明如何备份 A.R.C.H. 的 SQLite 数据库和知识资料 inbox，并在空数据目录或经过明确确认的替换操作中恢复它们。归档为 gzip 压缩 tar 文件，恢复时会验证文件清单和 SHA-256 校验和。

归档不包含 `data/auth/admin.json`、模型、运行时密钥、Caddy 本地 CA 或前端构建产物。管理员凭据必须按本机凭据管理流程单独保护；模型和 CA 应依照各自的安装步骤重新准备。

## 创建备份

为避免知识资料在复制时变化，先停止会写入数据或修改 inbox 的 A.R.C.H. 服务和手工操作。然后在仓库根目录运行：

    make backup

命令会输出新归档路径，默认类似：

    data/backups/arch-20261110T020304Z.tar.gz

每个归档文件名不能已存在，避免时间戳碰撞时静默替换之前的可恢复副本。命令会以 SQLite backup API 建立数据库一致性副本，并只复制 `data/knowledge/inbox/` 下的普通文件；包含符号链接的 inbox 会失败。将成功生成的归档复制到受控的离机位置，且不要提交到 Git。

## 恢复到空数据目录

恢复前停止 A.R.C.H.、Caddy 后端代理和可能访问该数据库的维护脚本。恢复会先在临时目录解压并验证清单；它拒绝路径穿越、链接、重复归档项、未列入清单的文件和校验和不匹配的文件。

当目标 `data/db/arch.sqlite3` 和 `data/knowledge/inbox/` 都不存在时，运行：

    make restore ARCHIVE=data/backups/arch-20261110T020304Z.tar.gz

恢复命令不会更改现有数据库或 inbox。若明确要以归档替换已有目标，先确认当前数据已有独立备份，再运行：

    make restore ARCHIVE=data/backups/arch-20261110T020304Z.tar.gz RESTORE_FLAGS=--replace

替换只影响归档中存在的数据库和 inbox。新内容会先在目标目录旁准备；若后续目标切换失败，已切换的先前目标会回滚。它不会删除不在归档中的另一个目标，也不会恢复管理员凭据或模型。

## 恢复后检查

先运行数据库 migration 并重新建立知识索引：

    make migrate
    make rag-index

随后按[首次在本机完成一次对话](../tutorials/first-local-conversation.md)启动服务并登录，确认会话、记忆审批和“本地知识库”状态符合预期。自动化测试已验证归档往返、校验失败拒绝和显式替换保护；尚未完成真实设备上的恢复演练或重启后服务恢复验收。
