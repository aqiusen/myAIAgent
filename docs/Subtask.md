# Subtask / spawn

> 严格对照 Suna `internal/subtask` + `internal/tools/agenttools`。
> 主 agent 派出孤立子任务：独立模型、独立上下文、缩小工具箱。

---

## 为什么要 spawn

主会话带着完整历史、Skill、全部工具。有些工作需要：

- 换一个模型（便宜的做检索、强的做 review）
- **不要**主会话的闲聊和偏见
- 只给只读工具，避免子任务乱改文件

Suna 的合同：子任务看见的只有 `task` / `context` / 被授权的工具。不继承主历史、图片、Skill 工作流。不能再 spawn，不能问用户。

---

## 工具 schema

```
spawn
  task     自包含任务（必填）
  model    精确模型 ref（必填，来自 Available subtask models）
  tools    允许的工具名列表（必填，可为 []）
  context  额外上下文（可选）
```

可授权工具 = builtin + MCP。`spawn` / `skill_load` / `skill_start` 不能下放。

---

## 子任务怎么跑

1. 新的空 Memory（只有 subtask system + 一条 user = task）
2. 新的 Runner，provider 用选定模型，tools_list 只有授权工具
3. Guard 仍在（run_command 还要审查）；ask 模式 fail-closed（不能问用户）
4. 最终必须交 JSON：

```json
{"result":"...","side_effects":{"status":"none|cleaned|remaining|unknown","summary":"...","paths":["..."]}}
```

解析失败标 `completed_unstructured`。运行失败标 `failed`，若用过工具则 side_effects 为 `unknown`。

---

## 代码

| Suna | 本项目 |
|------|--------|
| `internal/subtask/subtask.go` | `myagent/subtask.py` |
| `agenttools/provider.go` spawnSpec | `myagent/tools/spawn_provider.py` |
| `Agent.ExecuteSpawnTool` | `Agent.execute_spawn` |
| `CanGrantToSubtask` | `tools.base.can_grant_to_subtask` |

没有做 Suna 的 SubtaskFor 模型过滤、并发多个 spawn、子任务事件流进 TUI。一次 spawn 同步跑完，主循环看到一份 JSON。

---

## 验收

「用另一个模型独立 review 这段代码，只给只读工具」→ 主模型调 `spawn(model=..., tools=["read_file","search"], task=...)`，子任务读文件后返回 JSON result，主会话历史不会进子任务。
