# 当前进度

> 每次回来先看这份文档，再决定做什么。
> 详细设计看 [master-roadmap.md](master-roadmap.md)；对照 Suna 看 [suna-warmup.md](suna-warmup.md)。
>
> 最后更新：2026-09-13

---

## 怎么用

1. 看「现在在哪」——一句话现状。
2. 看「阶段仪表盘」——哪些完成、哪些缺口。
3. 看「下一步」——按推荐顺序选一个功能做。
4. 做完后：把对应项打勾、改仪表盘状态、更新日期。**这份文件是进度的唯一入口**，不要回头去改 master-roadmap 里的旧清单。

---

## 现在在哪

阶段 0–5 已走完（骨架、健壮性、记忆持久化、Guard、模型抽象、9 个内置工具 + MCP）。
阶段 9 的 Textual TUI 能用，但还在打磨。

真正还没学到的 Agent 概念：

- **Skill**（阶段 6）
- **Subtask / spawn**（阶段 7）
- **Session State 压缩**（阶段 2.3，记忆只做了一半）
- **daemon 进程分离**（阶段 8）

Suna 的产品形态是：daemon + TUI + agent loop + tools + Guard + compact memory + Skill + spawn。
本项目已覆盖前半段；缺的是后半段里真正改变能力边界的三块。

---

## 阶段仪表盘

对照 `docs/master-roadmap.md` 的 9 个阶段，以及本地 Suna（`~/workspace/suna`）。

| 阶段 | 主题 | 状态 | 对应 Suna | 实际情况 |
|---|---|---|---|---|
| 0 | 骨架 | 完成 | runner / agent / tools | 分层和模型-工具循环通了 |
| 1 | 健壮性 | 完成 | `internal/runner` | 流式、限流、重试、超时都有 |
| 2 | 记忆 | 完成一半 | `internal/memory` | SQLite + token 裁剪有了；**旧消息还是直接丢掉，没有 Session State 折叠** |
| 3 | Guard | 基本完成 | `internal/guard` | 四模式 + 敏感文件 + 审计有了；**没有 workspace 边界** |
| 4 | 模型抽象 | 完成 | `internal/model` | Provider + Registry + `/model` 切换 |
| 5 | 工具 + MCP | 完成 | builtin + mcp | 9 个内置工具 + stdio MCP client |
| 6 | Skill | 未做 | `internal/skill` | 没有 skill 目录、没有 `skill_load` |
| 7 | Subtask | 未做 | `internal/subtask` | 没有 `spawn`，没有独立上下文 |
| 8 | daemon | 未做 | daemon + protocol + transport | TUI 和 agent 还在同一个进程里 |
| 9 | TUI | 能用，未完成 | `internal/tui` | Textual 聊天、流式、历史恢复；正在做 `/model` 选择弹窗 |

---

## 手头未完成（WIP）

未提交改动：`myagent/tui.py`、`tests/test_tui.py`

- `/model` 从聊天区打印列表，改成和 `/setting` 一样的弹窗
- 修了 Modal 里按 Enter 会冒泡成聊天消息的问题

**先把这笔收完再开新功能。** 测过、提交，不要继续往界面里加东西。

---

## 下一步（按这个顺序选）

做完当前 WIP 之后，按学习价值排序。每次只开一项。

### 1. Skill（阶段 6）—— 推荐下一个新功能

对照 Suna `internal/skill` + `internal/tools/skilltools`。

要做的：

- Skill 是一个目录（`SKILL.md` + 可选脚本/资源）
- 模型先只看到短描述
- 需要时调用 `skill_load`，把完整指令注入上下文
- 预置 1–2 个（比如 code-review、写测试）

验收：输入「帮我 review 这段代码」，agent 自动加载 code-review skill，而不是把方法写死在 system prompt 里。

为什么现在做：路线图下一站，边界清晰，体积不大。现在工具只能「做事」，还不能「按需加载工作方法」。

### 2. Session State 压缩（阶段 2.3）

`memory.py` 里已标注「进阶：对丢弃的旧消息做 LLM 摘要」。Suna 的做法：

```
WorkingMemory = 最近对话(keepRegion) + Session State(旧内容折叠)
```

结构固定：Active context / Completed work / User requirements / Tool facts / Open threads。

这是阶段 2 唯一没做完的核心课：从「会忘」变成「会折叠」。长对话一多，硬裁剪会把早期决策丢掉。

可插在 Skill 之后、Subtask 之前做。

### 3. Subtask / `spawn`（阶段 7）

Suna 的招牌能力。本项目已有模型注册表，正好用上。

要做的：

- 主 agent 调 `spawn(model, task, context, tools)`
- 子任务**不继承**主会话历史、记忆、完整工具箱
- 返回结构化结果 + 副作用披露
- 子任务不能再 spawn、不能问用户

验收：「用另一个模型独立 review 这段代码，只给只读工具」。

### 4. daemon + 协议（阶段 8）—— 最后做

Suna 的 TUI 是独立客户端，走 JSON-RPC 连 daemon。本项目界面和业务还在一个进程里。

先把 Skill / Subtask / compact 做进同一个 `Agent`，再拆进程，迁移成本更低。现在拆等于能力没齐就先搬家。

---

## 现在不要做

这些有用，但不是下一课：

- 把 TUI 做成 Suna 那样的多页面客户端（会话页、AskUser、附件、Skill overlay）
- Anthropic 原生 adapter（OpenAI 兼容端点已经够用）
- TOML 配置、结构化日志、自更新（阶段 9 发行打磨）
- 并发跑工具、token calibrator（runner 保护层，不是新概念）
- Guard workspace 边界（有空可补，优先级低于 Skill / Subtask）

---

## 勾选清单

做完一项就改成 `[x]`，并更新上面的仪表盘和「现在在哪」。

```
[x] 阶段 0    骨架
[x] 阶段 1.1  工具输出限流
[x] 阶段 1.2  流式输出
[x] 阶段 1.3  结构化错误 + 重试
[x] 阶段 1.4  超时控制
[x] 阶段 2.1  token 裁剪
[x] 阶段 2.2  大结果不占上下文
[ ] 阶段 2.3  上下文压缩 Session State
[x] 阶段 2.4  SQLite 持久化
[x] 阶段 3.1  命令分级 + 白名单
[x] 阶段 3.2  风险分级
[x] 阶段 3.3  Guard Mode (readonly/ask/auto/smart)
[x] 阶段 3.4  敏感文件拦截 + 审计
[x] 阶段 3.5  smart + LLM 审查
[ ] 阶段 3.x  Guard workspace 边界          （有空再补）
[x] 阶段 4    模型抽象 adapter/router
[x] 阶段 5.1  更多内置工具
[x] 阶段 5.2  结构化 schema
[x] 阶段 5.3  MCP 集成
[ ] 阶段 6    Skill 系统                   ← 下一个新功能
[ ] 阶段 7    Subtask / spawn
[ ] 阶段 8    daemon + protocol + transport
[ ] 阶段 9    TUI 打磨：先收完 /model 弹窗，然后停
```

---

## 做完功能后怎么更新这份文件

1. 改「最后更新」日期。
2. 勾选清单对应项。
3. 改仪表盘那一行的「状态 / 实际情况」。
4. 重写「现在在哪」和「手头未完成」。
5. 如果推荐顺序变了，改「下一步」。
