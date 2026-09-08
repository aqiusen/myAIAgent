# 扩展路线图

骨架已通：一个能对话、能调用工具的 Python agent。以下按价值/依赖排序，逐步加能力。
每一步都对应 Suna 里的一个模块，方便你对照原项目学习。

## 阶段 1：让体验变好（必做）

1. **流式输出**：把 `_one_call` 改成 `stream=True`，边生成边打印，体验提升巨大。
   —— 对应 Suna runner 的流式适配。
2. **上下文按 token 数裁剪**：目前是"留最近 N 条"，改为估算 tokens、
   超过阈值时裁剪。更省、更稳。（进阶：对旧消息做摘要压缩，对应 Suna `internal/memory`。）
3. **持久化会话**：用 SQLite（Python `sqlite3` 标准库即可）保存历史，
   下次启动能接着聊。—— 对应 Suna `internal/memory` + `internal/media`。

## 阶段 2：安全与健壮（必须认真对待）

4. **命令执行安全（最重要）**：目前 `run_command` 直接 `shell=True` 执行，
   **危险**。必须加白名单 / 危险命令拦截 / 用户二次确认。
   —— 这正是 Suna 的 **Guard** 模块做的事，强烈建议研究 `internal/guard`。
5. **工具异常与重试**：超时、网络错误的优雅处理。

## 阶段 3：能力扩展（可选，按兴趣）

6. **MCP**：接入标准化的外部工具协议（MCP 服务器），你的 agent 就能用上百种现成工具。
   —— 对应 Suna `internal/mcp`。
7. **Skill**：把"可复用的工作方法"存为 skill，按需加载。
   —— 对应 Suna `internal/skill`。
8. **Subtask**：让主 agent 能开启一个独立上下文、可指定不同模型的子任务。
   —— 这是 Suna 的招牌能力，对应 `internal/subtask`。

## 阶段 4：形态升级（对应 Suna 的 daemon + TUI）

9. **引入 server/daemon 划分**：把 agent 放进常驻服务，用 JSON-RPC 通信，
   分离"界面进程"与"业务进程"。—— 对应 `internal/transport` + `internal/protocol`，这是大工程。
10. **终端 TUI**：用 `textual` 做一个好看的终端界面，替代目前的命令行 REPL。
    —— 对应 Suna 的 `internal/tui`（它用 Go 的 Bubble Tea）。

## 学习建议顺序

- 先把**阶段 1 的 #1 流式**和 **#4 命令安全**做了——一个提升体验，一个堵上危险，性价比最高。
- 然后精读 Suna 的 `internal/guard` 和 `internal/runner`，对照本项目理解它的实现。
- 等你把阶段 3 的能力都装过一遍，对 agent 的全局理解就成型了。
