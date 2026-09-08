# 架构与分层

## 为什么把代码拆成这么多模块？

你的第一个反应可能是：一个简单的聊天 agent，为什么不能全写在一个文件里？

这个项目的核心结论（也是从 Suna 学到的最重要一课）是：

> **把"界面/入口"、"模型调用"、"工具执行"、"历史记忆"、"配置"分开，不是过度设计，
> 而是为了让每一块都能独立演进、独立测试、独立替换。**

## 分层图

```
main.py           入口，只负责启动（薄）
  ↓
myagent/cli       跟用户打交道 + 打印（以后换成 TUI 只改这一层）
  ↓
myagent/agent     编排：串起下面所有模块，对外只暴露 run()
  ├── memory      历史消息 + 上下文裁剪
  ├── runner      模型调用循环（心脏）
  └── tools       工具声明 + 执行（read_file / list_dir / run_command）
  └── config      配置与凭据读取
```

## 每个模块的单一职责

| 模块 | 只负责 | 对应 Suna |
|---|---|---|
| `config.py` | 配置从哪来、默认值 | `internal/config` |
| `memory.py` | 存历史、决定发多少 | `internal/memory` |
| `tools/base.py` | "工具长什么样"（声明） | `internal/tools` |
| `tools/builtin.py` | 具体有哪些内置工具 + 执行 | `internal/tools/builtin` |
| `runner.py` | 模型-工具往返循环 | `internal/runner` |
| `agent.py` | 把上面串起来 | `internal/agent` |
| `cli.py` | 命令行交互 | `internal/tui` |

## 三条关键边界（务必理解）

**1. 依赖方向是单向的。** 从 `cli → agent → {memory, runner, tools, config}`。
`runner` 不知道 `agent` 的存在，`agent` 不写具体工具逻辑。这样改任何一个模块都不会牵动其它。

**2. "工具声明" 与 "工具实现" 分离。** 模型只看到 `tool.schema`（名字/描述/参数），
永远看不到执行代码。这让你可以随意增删工具、甚至换成别的语言实现，都不影响模型协议。

**3. 入口越薄越好。** `main.py` 只做启动。真正的行为都在包里。
这让你能从 CLI 无缝换到网页/TUI/API 服务，而业务代码一行不用改。

## 为什么不把 daemon / 进程分开（与 Suna 的差异）

Suna 把系统拆成「TUI + protocol transport + daemon」三部分，因为它是多客户端、多会话、
需要长期后台服务的产品。**这是进阶形态，不是第一步该做的。**
第一步先跑通单体进程里的逻辑分层；等你需要"多个界面共用同一个 agent"、
或"后台服务独立于界面运行"时，再引入 `server/client` 划分才水到渠成。
过早分层会造成不必要的复杂度。
