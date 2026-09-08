# Textual 界面升级

> 记录：为什么把界面从 prompt_toolkit REPL 升级到 Textual 完整 TUI。
> 目的、遇到的问题、升级后的好处，一次说清。

---

## 一、为什么升级

### 问题：prompt_toolkit 界面太朴素

prompt_toolkit 只负责"读输入"，界面是纯文本 REPL：

```
牛逼大森哥:> 今天日期是什么
Agent> 今天是 2026年9月9日，星期三。
----------------------------------------
```

- 没有消息分区、没有颜色、没有边框
- 工具调用和回答混在一起，看不清
- 没有滚动、状态栏、快捷键提示

对**成品 agent** 来说，这样的界面不够专业。

### 目标：做出 Suna 那样的漂亮 TUI

Suna 用 Bubble Tea（Go 的 TUI 框架）做界面。Python 生态里对应的就是 **Textual**。

---

## 二、为什么选 Textual

| 库 | 类型 | 特点 |
|------|------|------|
| **Textual** ⭐ | 完整 TUI 框架 | 富组件、CSS 样式、响应式布局，最接近 Bubble Tea |
| Rich | 富文本/格式化 | 只美化输出，不是完整 TUI |
| prompt_toolkit | 输入库 | 只处理输入，界面朴素 |

Textual 是 Python 里做完整 TUI 的事实标准，和 Suna 的 Bubble Tea 架构对应
（事件驱动、组件化、CSS 样式）。

---

## 三、界面设计

```
┌─ myAIAgent — 本地代码 Agent ─────────────────────────────┐
│ myAIAgent 已启动。输入你的问题，输入 /quit 退出。          │
│                                                          │
│ 你> 今天日期是什么                                        │
│ Agent> 今天是 2026年9月9日，星期三。                      │
│                                                          │
│ ──────────────────────────────────────────────────────── │
│ 输入你的问题，/quit 退出 █                               │
└──────────────────────────────────────────────────────────┘
```

- **Header**：标题 + 副标题
- **聊天区**：消息分区（用户/助手/系统/工具），带颜色和边框
- **输入框**：底部 dock，自动聚焦
- **Footer**：快捷键提示

---

## 四、架构：如何不卡 UI

Agent 的 `run()` 是**同步阻塞**的。如果直接在 UI 线程跑，界面会卡死。
Textual 用 **worker 线程**解决：

```python
@work(thread=True)
def _run_agent(self, text):
    def on_delta(delta):
        self.call_from_thread(self._append_stream, delta)  # 线程安全更新 UI
    self.agent.run(text, on_delta=on_delta)
```

- `@work(thread=True)`：在后台线程跑 agent，不阻塞 UI
- `call_from_thread`：从线程安全地更新 UI（流式输出）
- 流式输出：增量追加到 Static 组件，实时显示

---

## 五、代码结构

```
myagent/
├── tui.py        # Textual 聊天界面（ChatApp）
├── cli.py        # 薄启动器：加载配置 → 建 agent → 启动 TUI
├── agent.py      # 编排（不变）
├── runner.py     # 模型-工具循环（不变）
└── guard.py      # 安全 Guard（不变）
```

界面层（tui.py）和业务逻辑（agent/runner/guard）完全解耦，符合分层架构。

---

## 六、验证结果

- ✅ ChatApp 构建成功
- ✅ 界面正常渲染（标题、系统消息、聊天边框）
- ✅ 流式输出正常（agent.run 的 on_delta 回调）
- ✅ Guard 正常工作（smart 模式 LLM 审查）

---

## 七、代价与权衡

- **新增依赖**：`textual`（含 rich 等）
- **ask 模式确认**：TUI 里默认拒绝（fail-closed），避免线程交互复杂度。
  默认 smart 模式用 LLM 审查，不依赖用户确认。如需 ask 模式确认，后续可加模态框。

---

## 八、一句话总结

**prompt_toolkit 只解决输入，界面朴素；Textual 是完整 TUI 框架，
用 worker 线程跑 agent + 流式更新 UI，做出专业漂亮的聊天界面，
是成品 agent 界面层的正确选择。**
