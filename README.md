# Local Voice Agent Workspace

本工作区用于实现一个单用户、全流程本地运行、可通过局域网浏览器访问的实时语音 Agent。Milestone 0 已建立工程骨架和健康检查；语音模型、浏览器通话、记忆、RAG、工具和数字人尚未实现。

权威文档：

- 已确认设计：`.agents/local-voice-agent/DESIGN.md`
- 实施与验收计划：`execplan/local-voice-agent.md`
- 开发与验证命令：`docs/DEVELOPMENT.md`

下一位执行者应先完整阅读 ExecPlan，从 Milestone 0 开始，并在每个停止点更新其中的 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。不要跳过原型验证，不要在首个里程碑提前引入数字人模型。

## 当前状态

截至 2026-11-08，需求和架构已经确认，Milestone 0 正在建立可复现的开发环境。目标机器是 Ubuntu 24.04、RTX 4090 24GB、Ryzen 7 9700X、32GB 内存，当前已释放约 100GB 磁盘空间。

## 固定目标

- 运行时 ASR、LLM、TTS、记忆、RAG 和工具编排全部在本机完成。
- 浏览器从局域网通过 HTTPS 和 WebRTC 使用麦克风。
- 单用户、同一时间一个语音会话。
- AI 播报期间持续监听；用户插话后 250ms 内停止输出。
- 用户停止说话后 1.5 秒内开始播放首段回答，性能不足时必须用测量结果说明瓶颈。
- 首批工具为受控本机终端和联网搜索。搜索可以访问互联网，但不调用云端推理模型。
- 只读低风险命令可自动执行；修改状态的命令必须在浏览器确认；高风险命令拒绝。
- 长期记忆先生成候选，只有用户在浏览器确认后才入库。
- RAG 只索引工作区内专门的数据目录。
- 数字人必须保留标准适配器边界，先实现空适配器，后续接入 Live2D/VRM、LiveTalking/MuseTalk 或 CyberVerse/FlashHead。
