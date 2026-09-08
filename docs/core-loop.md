# 核心循环详解（本项目的"心脏"）

如果你只读一篇文档，就读这一篇。理解了这个循环，你就理解了 agent 的本质。

## 一个关键事实

每次调用大模型，你都要把**完整上下文**（系统提示 + 你的问题 + 历史 + 工具结果）再发一遍。
模型不记得上次说了什么。所以 agent 的"思考过程"，其实是**多次模型调用串起来的结果**。

## 循环长这样

```
用户输入 "看看 main.py 里有什么"  → agent.run()
──────────────────────────────────────
第 1 轮模型调用：
  我发送: system + "看看 main.py 里有什么"
  + tools 声明 [read_file, list_dir, run_command]
  模型回复: content 为空 + tool_calls = [read_file(path="main.py")]
        └─ 意思是"我要调 read_file，参数为 main.py"

我执行 read_file，得到文件内容，把结果当作 role=tool 的助手消息塞回：
  [assistant 带 tool_calls] + [tool 角色，tool_call_id=xxx, content=文件内容]

──────────────────────────────────────
第 2 轮模型调用：
  我发送: system + 用户问题 + 上面的 assistant(tool_calls) + tool(结果)
  模型这下能看到文件内容，回复: content="main.py 的作用是...", 无 tool_calls
        └─ 意思是"我有答案了，不用再调工具"

我拿到纯文字答案，agent.run() 返回。
```

## 关键实现要点（对应 runner.py 的代码）

1. **最大轮数保护 `MAX_ITERATIONS`**：万一模型反复调工具不收尾，不能无限烧钱。
2. **工具调用的结构**：模型返回的 `tool_calls` 是一个列表，每个有 `id`、
   `function.name`、`function.arguments`(JSON 字符串)。执行后必须原样带回 `tool_call_id`，
   否则模型不知道这次调用对应哪个。
3. **结果要"回填"**：工具结果必须以 `role="tool"` 的消息放回对话，且要带上上次的
   assistant 消息（含 tool_calls）。这样模型才能"看到"执行结果。
4. **解析参数**：`arguments` 是 JSON 文本，用 `json.loads` 转成 dict，再用 `**kwargs` 传给工具函数。

## 三种"模型想要工具"时循环结束的方式

| 情况 | 循环行为 |
|---|---|
| 模型直接给文字，无 tool_calls | 直接返回，结束 |
| 模型要工具 → 我们执行 → 回填 → 再调 | 继续循环 |
| 达到 MAX_ITERATIONS | **截断**，返回最后一次得到的文字（若为空返回空串） |

> 真实工程里，MAX_ITERATIONS 用满后应该给用户一个"达到了最大操作轮数"的提示，
> 这里刻意先留简单版本，作为你练习改进的点。
