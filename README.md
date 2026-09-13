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
│   ├── guard.py              安全 Guard        ← internal/guard
│   ├── tui.py                Textual 聊天界面   ← internal/tui
│   ├── tools/
│   │   ├── base.py           工具抽象/声明      ← internal/tools
│   │   └── builtin.py        内置工具(文件/命令) ← internal/tools/builtin
│   └── cli.py                命令行入口         ← internal/tui
└── docs/                     学习文档
    ├── architecture.md       为什么分层
    ├── tech-decisions.md     选型原因
    ├── core-loop.md          核心循环(必读)
    ├── roadmap.md            下一步扩展
    ├── prompt_toolkit使用原因.md  输入层升级记录
    ├── 安全Guard实现.md           Guard 设计记录
    └── Textual界面.md            TUI 界面升级记录
```

## 快速开始

```bash
cd my-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1) 复制配置模板，填入你的 API key（支持任何 OpenAI 兼容端点）
cp .env.example .env
# 编辑 .env，把 MY_AGENT_API_KEY 换成你的真实 key
#   MY_AGENT_API_KEY=你的key
#   MY_AGENT_MODEL=gpt-4o-mini      # 或 deepseek-chat 等
#   MY_AGENT_BASE_URL=https://api.openai.com/v1

# 2) 直接运行（无需每次 export，配置自动从 .env 读取）
.venv/bin/python main.py
```

进入后直接对话，例如：
```
你> 列出当前目录的结构
你> 读一下 main.py 并总结它做了什么
你> 今天日期是什么
你> /quit
```

> 说明：`.env` 含密钥，已被 `.gitignore` 忽略，不会提交到仓库。
> 也可以不建 `.env`，改用 `export` 环境变量（环境变量优先于 `.env`）。

## 运行测试

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -v
```

覆盖：Store 持久化、Memory token 裁剪、Guard 安全审查、Agent 持久化集成、流式输出。

## 推荐阅读顺序

1. `docs/progress.md` —— 当前进度：做到哪了、下一步做什么（每次回来先看）
2. `docs/core-loop.md` —— 理解 agent 的心脏（模型-工具循环）
3. `docs/architecture.md` —— 理解为什么分层
4. 读 `myagent/` 代码（都有详细注释）
5. `docs/tech-decisions.md` —— 理解选型背后的为什么
6. `docs/master-roadmap.md` —— 完整 9 阶段规划；进度以 progress.md 为准

## 安全提示

本项目包含 `run_command`（执行 shell 命令）工具，**仅用于本地学习**。
已内置安全 Guard（`myagent/guard.py`，参考 Suna 的 `internal/guard`）：

- 分层拦截危险命令（`rm -rf /`、`curl x | sh` 等）
- 拒绝读写敏感文件（`.env`、`.pem`、`id_rsa` 等）
- 四种模式：`readonly` / `ask` / `auto` / `smart`（默认 smart，用 LLM 审查命令）
- 全程审计留痕（`MY_AGENT_GUARD_AUDIT` 指定日志路径）

详见 `docs/安全Guard实现.md`。
