# 构建一个可用的 Agent —— 完整开发路线图（Master)

> 目的：不是复刻 Suna，而是通过**亲自动手**做出一个**真正能用、好用、架构完整**的
> 本地 Agent，从而掌握 Agent 开发中的**每一个环节**。
>
> 现状：my-agent 已有最小骨架（config / memory / agent / runner / tools / cli），能对话、能调 3 个工具。
> 本路线图在此基础上，带你从"能跑的原型"一步步升级到"像 Suna 一样完整的产品"。
>
> 语言选择：Python（先求理解，语言无关）。每阶段的 Suna 模块是学习参考，不是照抄。

---

## 总目标：最终架构（对应 Suna）

学完全部阶段后，你的 my-agent 会长成这样：

```
┌────────────────────────────────────────────────────────┐
│ 客户端层  TUI（终端界面） / 脚本 / 未来扩展               │
│    │  JSON-RPC / NDJSON / 本地 transport                │
│    ▼                                                    │
│ 常驻服务层  daemon（共享运行时常驻后台）                  │
│   ├─ model 目录、providers、router/adapter               │
│   ├─ tools 目录 + Guard + MCP                           │
│   ├─ memory（压缩 / Session State / 持久化）             │
│   ├─ Skill                                                │
│   └─ sessions（每会话独立模型 + working 状态 + subtask） │
└────────────────────────────────────────────────────────┘
```

分层的价值（你已经在架构文档里学过）：**界面、模型调用、工具、记忆、安全、配置各自独立演进。**

---

## 学习路径总览（9 个阶段）

| 阶段 | 主题 | 学到什么 Agent 概念 | Suna 参考 |
|---|---|---|---|
| **0** | 骨架（已完成） | 分层、模型-工具循环 | runner/agent/tools |
| **1** | 健壮性 + 体验 | 重试/流式/限流/超时/错误分类 | internal/runner |
| **2** | 记忆升级 | token 裁剪、压缩、持久化 | internal/memory |
| **3** | 安全 Guard | 风险分级、白名单、审计 | internal/guard |
| **4** | 模型抽象 | router/adapter、多提供商 | internal/model |
| **5** | 工具生态 | 更多工具 + MCP 协议 | internal/tools,mcp |
| **6** | Skill | 可复用工作方法按需加载 | internal/skill |
| **7** | Subtask | 独立上下文子任务 | internal/subtask |
| **8** | 进程架构 | daemon + protocol + transport | internal/daemon |
| **9** | TUI + 打磨 | 终端界面、配置、日志、发行 | internal/tui 等 |

> 每阶段都有「学习目标 / 交付物 / 验收标准」。**做完一个验证一个再进入下一个。**

---

## 阶段 0：骨架（已完成 ✅）

**交付物（已有）：**
- 分层：`config → cli → agent → {memory, runner, tools}`
- 核心模型-工具循环通
- 3 个内置工具

**进入下一阶段前自查：**
- [x] 能对话
- [x] 能调工具（read_file / list_dir / run_command）
- [x] 理解了循环和分层原理

---

## 阶段 1：健壮性 + 体验（让 agent "稳"且"活"）

**学习目标：** 一个能上线的模型调用循环，不是在每环加保护。

### 1.1 工具输出限流（★ 最先做，止损）
- Suna：`maxToolOutputLines=500`、`maxToolOutputBytes=50KB`，超出截断并注明。
- **交付**：`tools/base.py` 加 `truncate_output`，所有工具结果进上下文前截断。
- **验收**：`run_command` 输出 1MB 也只把 500 行 / 50KB 给模型，其余丢掉。

### 1.2 流式输出（让 agent "活"起来）
- Suna：`readStream` 按 chunk 累加 + 空闲超时。
- **交付**：`runner._one_call` 支持 `stream=True`，`cli` 边生成边打印。
- **验收**：输入"写一首诗"，能看到逐字输出，而非等待后一次性出现。

### 1.3 结构化错误 + 重试
- Suna：`ModelError{Kind, StatusCode}`，靠状态码判断重试（429/500/502/503/504/408/网络错）。
- **交付**：`runner` 里封装重试逻辑（指数退避，最多 3 次），认证类错误不重试。
- **验收**：临时断网/服务端 500 时自动重试后成功，而不是崩掉。

### 1.4 超时控制
- **交付**：每次模型调用、工具执行设超时；用户 Ctrl+C 能中断。
- **验收**：模型一直不返回会超时中断，而不是无限等。

### 本阶段结束标志
一段对话里：流式输出 + 大输出被截断 + 偶发网络错误自动恢复。

---

## 阶段 2：记忆升级（从"会忘"到"有记忆"）

**学习目标：** 记忆不是删旧消息，而是按预算保留 + 把旧内容压缩成状态账本。

### 2.1 按 token 数裁剪
- 现在是"留最近 N 条"。改成：估算 token，超阈值（如窗口 80%）才裁。
- **交付**：`memory.py` 用简单 token 估算函数。

### 2.2 大工具结果不占上下文
- **交付**：工具结果按 token 估算限制，过大时只保留摘要进模型上下文。
- Suna 参考：`compress.go` 的 `TruncateToolOutputForContext`。

### 2.3 上下文压缩（Session State）★ 核心进阶
- Suna：`WorkingMemory = 最近对话(keepRegion) + Session State(旧内容折叠)`
  - `Active context` 当前在做什么
  - `Completed work ledger` 已完成任务/话题
  - `User requirements` 用户要求/纠错/决策
  - `Tool facts` 工具事实
  - `Open threads` 未完成事项
- **交付**：上下文超预算时，把旧消息送给一个 LLM 总结成 Session State，保留最近一段。
- **验收**：聊很久后，早期话题仍能通过 Session State 模糊回忆，token 占用明显下降。
- Suna 参考：`internal/memory/compress.go` + `plans/06-memory.md`。

### 2.4 SQLite 持久化
- **交付**：用标准库 `sqlite3` 保存会话，重启后能从上次继续。
- **验收**：退出程序再启动，还能看到之前的对话。

### 本阶段结束标志
长对话 token 受控、能接续上文、重启不丢会话。

---

## 阶段 3：安全 Guard（命令安全，必须认真对待）

> ⚠️ 你的 `run_command` 现在 `shell=True` 裸奔。这一步之前，**不要把 agent 暴露给不受信任的输入**。

**学习目标：** 安全是分层决策管道 + 硬规则永远兜底（fail-closed）。

### 3.1 命令分级 + 白名单
- **交付**：内置只读白名单（ls/cat/grep/...），只读命令直接放行；写入/危险命令标记风险。
- Suna 只读白名单参考：`plans/04-guard.md`。

### 3.2 风险分级 low/medium/high
- **交付**：`exec`（低=可证明只读 / 中=有副作用 / 高=删除格式化系统配置）。
- **验收**：`rm -rf /` 判 high 并拦截；`ls` 判 low 直接放行。

### 3.3 Guard Mode 策略
- **交付**：实现 `readonly` / `ask` / `auto` / `smart` 四模式。
  - `ask`（默认）：低风险自动过，中高风险问用户 → 对应 CLI 里的 Y/N 确认。
- **验收**：运行危险命令前，CLI 弹出确认，用户拒绝对话终止。

### 3.4 敏感文件拦截 + 审计
- **交付**：拦截凭证/密钥/SSH 目录读写；每次决策写入 SQLite 审计表。
- Suna 参考：`internal/guard/sensitive.go`、`audit.go`。

### 3.5 （进阶）smart mode + LLM 审查
- 让 LLM 判断工具调用是否服务当前任务（approve/reject）。
- 但 **硬规则永远优先，LLM 不能绕过 blocked/workspace/sensitive**。

### 本阶段结束标志
非只读操作要么自动放行（白名单）、要么用户确认，敏感文件一律拦截，全部留痕。

---

## 阶段 4：模型抽象（多提供商支持）

**学习目标：** 别让"厂商适配"侵入核心循环。

### 4.1 定义通用 Adapter 接口
- **交付**：`model/` 定义统一接口（`complete(messages, tools) -> stream of chunks`），
  OpenAI / Anthropic / 兼容端点各实现一个 Adapter。
- **验收**：切换模型只改配置，不改 runner 逻辑。

### 4.2 结构化错误模型
- **交付**：统一 `ModelError(details)`，Provider 的异常转成结构化错误，供阶段1重试使用。

### 4.3 Router 选择
- **交付**：按配置/用途选择 adapter（`spawn` 子任务可指定不同模型）。

### 本阶段结束标志
一个配置能切换 OpenAI / DeepSeek / 本地 Ollama，核心循环不动。

---

## 阶段 5：工具生态 + MCP

**学习目标：** 工具的"声明"与"实现"分离，能用标准协议接入外部工具。

### 5.1 更多内置工具
- **交付**：增加 `write_file`、`search`、`http`（GET 只读）、`read_image` 等。
- **验收**：agent 能读、写、搜索、联网。

### 5.2 结构化工具 Schema
- **交付**：不用 `Tool.make` 猜类型，改为每个工具手写完整 JSON Schema。

### 5.3 MCP 协议集成 ★ 大开眼界
**学习目标：** MCP（Model Context Protocol）是标准化的外部工具协议，让 agent 用上百种现成工具。
- **交付**：实现一个极简 MCP client（JSON-RPC over stdio），能连到一个 MCP server 拿工具。
- **验收**：把文件系统 / 数据库等现成 MCP server 的工具接入 agent。
- Suna 参考：`internal/mcp`、`internal/tools/mcptools`。

### 本阶段结束标志
agent 既能用本地工具，也能用 MCP 标准协议的工具。

---

## 阶段 6：Skill（可复用工作方法）

**学习目标：** Skill = 把"可复用的工作方法"（如 code review、refactor、写测试）存下来按需加载。

### 6.1 Skill 模型
- **交付**：Skill 是一个目录（含元信息 + 指令/步骤），加载后变成可调用的工具。
- Suna 参考：`internal/skill` + `internal/tools/skilltools`。

### 6.2 按需加载 + 描述路由
- **交付**：模型看到 skill 的描述，选择合适时机"加载并激活"该 skill。
- **验收**：输入"帮我 review 这段代码"，agent 自动加载 code-review skill。

### 本阶段结束标志
预置几个 skill，agent 能在合适时机调用它们。

---

## 阶段 7：Subtask（独立上下文子任务）★ Suna 招牌能力

**学习目标：** 主 agent 能开启独立上下文、可指定不同模型的子任务，且边界清晰。

### 7.1 为什么需要 subtask
- 主对话上下文可能被长任务污染；有的任务需要更贵/更便宜的模型。
- **交付**：给主 agent 一个 `spawn` 工具：传入 task/context/授权工具/模型，
  返回结构化结果（状态、文本、副作用披露）。
- Suna 参考：`internal/subtask` + `internal/tools/agenttools` + `docs/subtask.md`。

### 7.2 上下文隔离
- **验收**：subtask 只能看到显式传入的上下文，看不到主会话 history 和用户记忆。
- 这是 Suna 最强调的边界：**隔离而非共享，可审计而非失控。**

### 7.3 副作用披露
- **交付**：subtask 返回时披露它做过什么（调了哪些工具、改了哪些文件）。

### 本阶段结束标志
"帮我用 deepseek 独立分析这段代码并返回结论"能工作，且主上下文不被污染。

---

## 阶段 8：进程架构（daemon + protocol + transport）

**学习目标：** 把"界面"和"业务"分离，让多个客户端能共享同一个 agent 运行时常驻后台。

### 8.1 定义 JSON-RPC 风格协议
- **交付**：`protocol/` 定义请求/通知/响应的数据结构。
  - 事件语义分层：content 增量 / run 生命周期 / usage 统计。
- Suna 参考：`internal/protocol` + `docs/protocol.md`。

### 8.2 daemon 常驻服务
- **交付**：daemon 持有 model/tools/guard/memory/skills，`serve` 启动，后台运行。
- **验收**：CLI 起一个 daemon，脚本/第二客户端能连上它。

### 8.3 本地 transport
- **交付**：本地连接（Unix socket / stdio / TCP JSON），TUI/server 分离。

### 本阶段结束标志
一个 daemon、两个客户端能同时连、共享会话；TUI 退出 daemon 不退出。

---

## 阶段 9：TUI + 生产打磨

**学习目标：** 一个真正好用的终端界面和生产级配套。

### 9.1 TUI（textual）
- **交付**：用 `textual` 做：流式输出的消息列表、工具活动区、Guard 确认浮层、状态栏（用量）。
- Suna 参考：`internal/tui` + `plans/12-tui-design.md`。

### 9.2 配置升级
- **交付**：从环境变量升级到配置文件（TOML/ini），支持多 session、model 目录、guard 规则。

### 9.3 结构化日志
- **交付**：`logging` 模块，可查运行日志。
- Suna 参考：`internal/logging`。

### 9.4 发行打磨
- **交付**：错误处理、安装脚本 / requirements 固定、版本号管理、README。
- （可选）自更新：对应 Suna `internal/update`。

### 本阶段结束标志
一个完整可用、能配置、有日志、界面友好的本地 Agent 产品。

---

## 进度总表（复制到你的 TODO）

```
[ ] 阶段1.1 工具输出限流        ★ 先做
[ ] 阶段1.2 流式输出
[ ] 阶段1.3 结构化错误 + 重试
[ ] 阶段1.4 超时控制
[ ] 阶段2.1 token 裁剪
[ ] 阶段2.2 大结果不占上下文
[ ] 阶段2.3 上下文压缩 Session State
[ ] 阶段2.4 SQLite 持久化
[ ] 阶段3.1 命令分级 + 白名单
[ ] 阶段3.2 风险分级
[ ] 阶段3.3 Guard Mode (readonly/ask/auto/smart)
[ ] 阶段3.4 敏感文件拦截 + 审计
[ ] 阶段3.5 smart+LLM 审查       (进阶)
[ ] 阶段4  模型抽象 adapter/router
[ ] 阶段5.1 更多内置工具
[ ] 阶段5.2 结构化 schema
[ ] 阶段5.3 MCP 集成
[ ] 阶段6  Skill 系统
[ ] 阶段7  Subtask
[ ] 阶段8  daemon + protocol + transport
[ ] 阶段9  TUI + 生产打磨
```

---

## 三条贯穿始终的工程纪律

1. **每阶段做完必须测试**：gofmt/go fmt + 相关测试。Python 里就是 `python -m pytest` + 一个能跑的冒烟测试。
2. **单向依赖**：`cli → agent → {memory, runner, tools, config}`，谁都不越界。
3. **轻量克制**：能用标准库就用，别引一堆框架。每引入一个依赖，想清楚它值不值。
