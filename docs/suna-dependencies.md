# Suna 技术依赖分析：自研 vs 成熟开源方案

> 结论先行：**Suna 是「自研为核心 + 精选成熟库为辅」的混合模式。**
> 核心 agent 智力全部自研，底层基础设施（模型 SDK、TUI、SQLite、Shell 解析器）用成熟库。

---

## 一、Suna 直接用（外包）的成熟开源库

直接依赖共 14 个，按用途分：

| 领域 | 用的开源库 | 作用 | 对应本项目的 Python 概念 |
|---|---|---|---|
| **模型 SDK** | `openai-go` `anthropic-sdk-go` | 对接模型，不手写协议 | `openai` Python SDK |
| **终端 UI** | `bubbletea` `bubbles` `lipgloss` `glamour` | 完整 TUI 框架 | roadmap 里的 `textual` |
| **嵌入数据库** | `modernc.org/sqlite` | 纯 Go SQLite（记忆/审计/会话） | roadmap 里的 `sqlite3` |
| **Shell 解析** | `mvdan.cc/sh/v3/syntax` | 真正的 Shell AST 解析器（Guard 用） | 纯 Python 无对应 |
| **配置解析** | `BurntSushi/toml` | TOML 配置 | — |
| **剪贴板** | `golang.design/x/clipboard` | 剪贴板输入 | — |
| 工具 | `google/uuid` `yaml.v3` `go-winio` `x/sys` | UUID / Windows 底层等 | — |

### 两个最值得注意的选型

1. **模型 SDK**：Suna 没有手写 HTTP/SSE 协议，直接用官方 SDK。
   这印证了本项目 `docs/tech-decisions.md` 第 2 节的观点——别让"厂商适配"侵入核心循环。

2. **`mvdan.cc/sh`**：Suna Guard 判断"命令是否只读"用的是**真实 Shell 语法解析器**，
   不是正则猜。这是"结构性能证明"和"字符串匹配"的本质区别，
   学安全时会用到这个思路（即使 Python 里通常退化为白名单 + 简化解析）。

---

## 二、Suna 自己写的（核心智力，无现成库）

`internal/` 下约 300+ 个自研 Go 文件，业务包全自研：

| 包 | 文件数 | 自研内容 |
|---|---|---|
| `tui` | 109 | 终端渲染（基于 bubbles 但交互逻辑自研） |
| `tools` | 40 | 工具目录/执行路由 |
| `model` | 22 | Router + Adapter 抽象、错误模型、token 估算 |
| `agent` | 18 | 编排 |
| `guard` | 16 | 安全审查决策管道 |
| `daemon` | 15 | 常驻服务 |
| `memory` | 11 | 记忆压缩、Session State、user profile |
| `mcp` / `skill` / `protocol` / `transport` | 等 | 能力扩展与进程通信 |

这些**没有现成方案**，是产品的差异所在。

---

## 三、最重要的发现：Suna 刻意不用 Agent 框架

go.mod 里**没有** LangChain / LlamaIndex / CrewAI 这类 Agent 框架。
Suna 是**在裸模型 SDK 之上，自己手写整个 agent 循环**。

这印证了本项目的设计哲学（tech-decisions.md 第 6 节）：
> 不引入 Web 框架、ORM、pydantic、click。每一行代码你都看得懂、能改。

选型哲学一句话：

> **能用成熟库解决的就用（SDK、TUI、SQLite、Shell 解析器），
> 但 agent 的核心智力必须自己写（循环、记忆、安全、编排）——
> 因为"智力"才是产品差异所在，没有现成方案。**

---

## 四、对学习 my-agent 的启示

| 你的需求 | 该用现成的（像 Suna） | 该自己手写（像 Suna） |
|---|---|---|
| 连模型 | ✅ `openai` SDK | ❌ 别手写 SSE |
| 终端界面 | ✅ `textual` | ❌ 别手写 TUI |
| 存会话 | ✅ `sqlite3` | ❌ 别手写数据库 |
| 模型-工具循环 | ❌ | ✅ 手写（你已会） |
| 记忆策略 | ❌ | ✅ 手写 |
| 命令安全 Guard | ❌（只借底层解析器） | ✅ 决策管道手写 |
| 编排 / daemon / 能力扩展 | ❌ | ✅ 手写 |

---

## 五、结论

Suna 的价值不在"用了什么库"，而在于**它在哪些地方决定"自己写"**。

- 把「基础设施」（SDK / UI / DB / 解析器）外包给成熟库；
- 把「agent 的判断力」（循环 / 记忆 / 安全 / 编排）全部自研。

本项目学到这里，方向应该很清楚了：**先用少量成熟库搭基础设施，把核心循环和模块边界自己写明白。**
等你到 roadmap 阶段 3/4 做 MCP、TUI、daemon 时，也会自然走向同样的混合模式。
