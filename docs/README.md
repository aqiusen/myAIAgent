# my-agent 技术文档索引

这是一个**刻意模仿 Suna 分层**的最小 LLM Agent，目的是帮你学习真实 Agent 的代码应该怎么组织。

- [**当前进度**](progress.md) —— 每次回来先看：做到哪了、下一步做什么
- [**构建可用 Agent 完整路线图**](master-roadmap.md) —— 总规划：从骨架到像 Suna 一样完整，9 个阶段
- [架构与分层](architecture.md) —— 模块怎么拆、边界怎么划、为什么不能乱调
- [技术选型](tech-decisions.md) —— 每个选型背后的为什么（语言/SDK/工具调用/上下文）
- [核心循环详解](core-loop.md) —— 整个项目的心脏：模型-工具往返循环
- [扩展路线图](roadmap.md) —— 完成骨架后，下一步该加什么（对应 Suna 的 Guard/记忆升级等）
- [Suna 核心模块热身](suna-warmup.md) —— 精读 Suna 的 runner/memory/guard 心得，理解工业级和原型的区别
- [Suna 技术依赖分析](suna-dependencies.md) —— Suna 哪些用成熟库、哪些自己写，选型哲学
