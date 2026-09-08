# Suna 核心模块热身笔记

> 学习目标：精读 Suna 的 `internal/runner`、`internal/memory`、`internal/guard`，
> 理解「真实工业级 Agent」和「最小原型」的区别。对照本项目的 myagent 包理解。
>
> 源码位置：`$SUNA/internal/{runner,memory,guard}/`，设计文档：`$SUNA/plans/{04-guard,06-memory}.md`

---

## 模块一：internal/runner —— 心脏（模型-工具循环）

### 和你的 runner.py 相同的部分

你已经理解最基本版本的循环：

```
发消息 → 有 tool_calls 吗？
  ├─ 有 → 执行工具 → 结果回填为 role=tool → 继续循环
  └─ 无 → 这就是最终答案，返回
```

### Suna 在循环骨架之外加的 7 层保护

| 保护 | Suna 做法 | 为什么 | 对应你的 roadmap |
|---|---|---|---|
| ① 模型请求自动恢复 | `completeWithRecovery`：可重试错误退避 8s 重试 3 次 | 网络抖动不该让整个会话崩掉 | 阶段2 #5 |
| ② 流式处理 | `readStream`：按 chunk 累加 + 空闲超时 | 边生成边显示 | 阶段1 #1 |
| ③ 工具输出限流 | `maxToolOutputLines=500`, `maxToolOutputBytes=50KB` | 工具输出太长会撑爆上下文 | ★ 最该先补 |
| ④ 并发执行工具 | `go func()` 并发 | 多个工具并行更快 | 阶段3 |
| ⑤ 结构化错误重试 | `retryableModelRequestError` 看 `ModelError.StatusCode` | 不用字符串匹配 | 阶段2 |
| ⑥ token 估算校准 | `Calibrator` 用真实 usage 反馈修正系数 | 本地估算和真实 tokenizer 有偏差 | 阶段1 #2 |
| ⑦ 空闲/总超时 | `time.Timer` + ctx | 防模型卡死烧钱 | 阶段2 |

### 核心领悟

> 循环骨架和原型一样，但**每一环都加了保护**（重试/限流/超时/并发/校准）。
> 这就是「能跑的原型」和「能上线的产品」的区别。

---

## 模块二：internal/memory —— 你与 Suna 差距最大的地方

### 你的 memory.py：最原始的版本

一个列表 + "保留最近 N 条"。旧消息直接丢弃。

### Suna 的记忆系统：从"裁剪丢弃"升级为"压缩折叠"

关键认知（来自 plans/06-memory.md）：

```
旧内容不是删掉，而是浓缩成一个"状态账本"(Session State)，交给另一个 LLM 总结。
WorkingMemory = 最近几轮对话 (keepRegion) + Session State (旧内容折叠)
```

**A. working.go —— 线程安全 + 快照**
```go
func (w *WorkingMemory) Messages() []model.Message {
    // RLock 加锁
    copy(cp, w.messages)   // 返回拷贝，防外部篡改
}
```
你的 `snapshot()` 已返回拷贝 ✅。Suna 额外用锁保证并发安全（因为 runner 并行执行工具）。

**B. compress.go —— 压缩机制（最重要的一课）**
- 保留最近一段 `keepRecent`：**由代码确定性决定**，不交给 LLM（保证可测试、缓存友好）。
- 把更早的 `compressRegion` 送给 LLM，折叠成紧凑的 `Session State`。
- 结构固定（来自 plans/06-memory.md）：
  ```
  Active context          当前在做什么
  Completed work ledger   已完成任务/话题
  User requirements       用户明确要求/纠错/决策
  Tool facts              工具事实（读过/改过/跑过什么）
  Open threads            未完成事项
  Recovery note           未来如何接上
  ```

**C. 工具输出限流** 所有进模型的上下文都限流裁剪。

### 记忆的设计原则（plans/06-memory.md，必读）

记忆只服务 4 个目标：
1. 当前任务不中断
2. 较早内容不消失（折叠成 ledger 而非删除）
3. 恢复会话
4. 少量长期用户画像

> 注意：`user_profile_memory`（长期画像）和 `Session State`（当前会话状态）是**两个独立的东西**，
> 长期画像不保存项目细节/任务日志/实现细节。

### 核心领悟

> `裁剪丢弃` → `压缩折叠`。这是"会遗忘的 agent"和"有记忆的 agent"的本质区别。

---

## 模块三：internal/guard —— 安全审查（你的项目还没有）

### 你的现状（危险）

`run_command` 直接 `shell=True` 裸奔，一个不安全的命令就能搞坏系统。
这是 roadmap 阶段 2 最该补的。

### Suna 的分层决策管道（guard.go 的 Check 方法）

```
工具请求
  ▼ Stage 0: workspace 越界 → reject（最高优先级）
  ▼ Stage 1: 内置 blocked 危险命令规则 → reject
  ▼ Stage 2: sensitive 敏感文件(密钥/凭证) → reject (fail-closed)
  ▼ Stage 3: 用户 allowed 规则 → approve
  ▼ Stage 4: 风险分级 low/medium/high + mode 策略
  ▼ Stage 5: 只读判定（能证明只读→放行；不能证明→一律非只读）
  ▼ Stage 6: mode 决定 auto/ask/smart
```

### 4 种 Guard Mode

```go
ModeReadonly  // 只读，写操作一律拒
ModeAsk       // 低风险自动过，中高风险问用户   ← 默认，最安全
ModeAuto      // 全自动放行（只留硬拦截）
ModeSmart     // 中高风险让 LLM 审查意图
```

### 最精彩的：smart mode + LLM 审查

传统做法（静态规则 + 弹窗）的问题：**规则永远不完整，复杂意图无法用规则表达**。

Suna 做法：LLM 看「工具调用 + 脱敏参数 + 用户任务 + Agent 意图」，判断 approve/reject。
但 **硬规则永远兜底（fail-closed）**：

```go
reviewFallback：审查能力不可用时默认 reject，绝不放行
```

- LLM 只有审查权，**不能绕过** blocked / workspace / sensitive 硬规则。
- smart 只审 `exec`（最危险的 Act 工具），因为写文件本身不执行、危险在执行那步、执行必走 exec。

### 判断"命令是否只读"用了成熟库

Suna 用 `mvdan.cc/sh/v3/syntax`（真正的 Shell AST 解析器），
而不是正则猜，来证明命令无副作用。详见 `docs/suna-dependencies.md`。

### 核心领悟

> 安全不能只靠规则，但**硬规则必须永远兜底**。LLM 负责理解意图，规则负责绝对底线。

---

## 对照速查表

| 概念 | my-agent 现在 | Suna 的做法 | 优先级 |
|---|---|---|---|
| 模型-工具循环 | ✅ 已有 | + 重试/超时/并发 | 已有基础 |
| 流式输出 | ❌ | `readStream` | 阶段1 #1 |
| **工具输出限流** | ❌ | 500行/50KB 截断 | ★ 最高，先补 |
| 记忆 | 裁剪丢弃 | 压缩成 Session State | 阶段1 #2 |
| 命令安全 | ❌ 裸奔 | guard 分层管道 | 阶段2 #4 |
| SQLite 持久化 | ❌ | store.go | 阶段1 #3 |

---

## 三个"啊哈"时刻

1. **循环骨架你已经懂了**，Suna 的价值在每一环的保护（重试/限流/超时）。
2. **记忆不只是删旧消息，而是把旧内容压缩成状态账本**。
3. **安全不能用纯规则，但硬规则必须永远兜底**（fail-closed）。
