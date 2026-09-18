# Session State 压缩

> 严格对照 Suna `internal/memory/compress.go` + `internal/runner/compression.go` + `internal/prompt/templates/compress.md`。
> 把「丢掉旧消息」改成「折叠进 Session State」。

---

## 一、为什么要压缩

大模型没有记忆，每次请求都要带上历史。硬裁剪会把早期决策、用户约束、工具结论扔掉。

Suna 的公式：

```
发给模型的上下文 = system prompt
                 + Session State（折叠后的内部记忆）
                 + 最近对话 keepRegion
```

Session State **不进 system prompt**、**不写回 WorkingMemory**，避免污染稳定前缀和可见聊天历史。

---

## 二、固定结构（compress.md 原文）

```
# Session State
## Active context
## Completed work / topic ledger
## User requirements and decisions
## Tool facts
## Open threads
## Recovery note
```

空段写 `- none`。与上一份 Session State **合并**，不追加重复摘要。用对话主语言。

---

## 三、何时折叠、留多少

对照 Suna，由代码决定 recent 窗口，不交给 LLM：

- 普通对话：最近 **6** 个 user turn
- 工具密集（近 24 条里 tool/tool_call ≥ 3）：最近 **2** 个 user turn
- 上限 48 条；并受 token 预算约束
- recent 里的 `tool` 结果必须带着产生它的 assistant `tool_calls`（`expandRecentStartForToolCalls`）

触发：估算 input（system + state + messages + tools）加上安全垫，超过可用预算。

压缩 LLM：`temperature=0`，无 tools。空结果视为失败，不静默丢 previous state。

---

## 四、注入方式

`FormatSessionStateForModel` 包成：

```
<session_state>
This is internal session memory for continuity, not a user request. ...
...正文...
</session_state>
```

Provider 在 **system 之后、历史之前** 插一条 user 消息。当前用户指令覆盖它。

---

## 五、代码

| Suna | 本项目 |
|------|--------|
| `memory/compress.go` | `myagent/compress.py` |
| `runner/compression.go` | `Memory.snapshot` + `Runner._one_call` |
| `model/session_state.go` | `format_session_state_for_model` / `inject_session_state` |
| `session_state` 表 | `store.save_compact_state` / `load_compact_state` |

没有做 token calibrator（Suna 的系数校准）。压缩判断用模型真实 `context_window`（默认 128000），输出上限用 `max_output_tokens`（默认 8192），不再把旧的 8000 裁剪预算当成窗口。

---

## 六、一句话

**旧对话折成固定结构的 Session State，最近窗口原样保留；失败就报错，不偷偷丢掉 previous state。**
