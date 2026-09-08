# myAIAgent

一个用于**学习 LLM Agent 开发**的最小 Python Agent。代码刻意模仿 [Suna](https://github.com/alanchenchen/suna) 的分层结构，并附详细中文注释 + 技术文档。

> 目标不是复刻 Suna，而是通过一个最小可运行、单文件能读懂的项目，
> 让你掌握「模型调用循环 + 工具调用 + 上下文管理 + 分层架构」这些 agent 的核心知识。

## 项目结构（对照 Suna）

```
my-agent/
├── main.py                   入口（薄）
├── myagent/
│   ├── config.py             配置与凭据        ← internal/config
│   ├── memory.py             会话历史 + 裁剪    ← internal/memory
│   ├── agent.py              编排，暴露 run()   ← internal/agent
│   ├── runner.py             模型-工具循环(心脏) ← internal/runner
│   ├── tools/
│   │   ├── base.py           工具抽象/声明      ← internal/tools
│   │   └── builtin.py        内置工具(文件/命令) ← internal/tools/builtin
│   └── cli.py                命令行交互         ← internal/tui
└── docs/                     学习文档
    ├── architecture.md       为什么分层
    ├── tech-decisions.md     选型原因
    ├── core-loop.md          核心循环(必读)
    └── roadmap.md            下一步扩展
```

## 快速开始

```bash
cd my-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 设置凭据（支持任何 OpenAI 兼容端点）
export MY_AGENT_API_KEY=你的key
export MY_AGENT_MODEL=gpt-4o-mini      # 或 deepseek-chat 等
export MY_AGENT_BASE_URL=https://api.openai.com/v1

.venv/bin/python main.py
```

进入后直接对话，例如：
```
你> 列出当前目录的结构
你> 读一下 main.py 并总结它做了什么
你> /quit
```

## 推荐阅读顺序

1. `docs/core-loop.md` —— 理解 agent 的心脏（模型-工具循环）
2. `docs/architecture.md` —— 理解为什么分层
3. 读 `myagent/` 代码（都有详细注释）
4. `docs/tech-decisions.md` —— 理解选型背后的为什么
5. `docs/roadmap.md` —— 想继续深入时按它扩展

## 安全提示

本项目包含 `run_command`（执行 shell 命令）工具，**仅用于本地学习**。真实使用前
必须加上命令白名单 / 用户确认（对应 Suna 的 Guard，见 `docs/roadmap.md` 阶段 2）。
