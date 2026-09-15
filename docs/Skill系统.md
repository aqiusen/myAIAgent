# Skill 系统实现

> 参考 Suna 的 `internal/skill` + `internal/tools/skilltools`，
> 给 agent 加上「按需加载的工作方法」，而不是把方法写死在 system prompt 里。

---

## 一、为什么需要 Skill

工具解决「能做什么」（读文件、跑命令）。Skill 解决「按什么方法做」。

没有 Skill 时：code review / 写测试的步骤只能塞进系统提示词，上下文又贵又容易过时。

有 Skill 后：

- 模型平时只看到短描述（name + description）
- 真正需要时调用 `skill_load`，把 `SKILL.md` 全文注入这一轮对话
- 用户往 skills 目录丢一个目录就能扩展方法，不必改 agent 代码
- 首次启动把主目录里 Codex / Pi / Claude 等技能按同名拷进本 agent，之后不再每次扫描

这和 Suna 的设计一致：**索引轻、正文按需、硬规则兜底、启用要用户点头。**

---

## 二、参考 Suna 的架构

| Suna | 职责 | 我们的实现 |
|------|------|-----------|
| `internal/skill/metadata.go` | 只读 frontmatter | `myagent/skill/metadata.py` |
| `internal/skill/skill.go` Manager | 扫全局目录、Load、Check | `myagent/skill/manager.py` |
| `internal/skill/catalog.go` | project Skill 发现 + 摘要 | `myagent/skill/catalog.py` |
| `internal/skill/runtime.go` | records / 启用 / 审查入口 | `myagent/skill/runtime.py` |
| `internal/skill/review.go` | 收集审查文件 | `myagent/skill/review.py` |
| `internal/skill/workflow.go` | skill_start 问答流程 | `myagent/skill/workflow.py` + `Runtime.start` |
| `internal/skill/import.go` | 本地 / zip / git 导入 | `myagent/skill/importer.py` |
| `internal/tools/skilltools` | `skill_load` / `skill_start` | `myagent/tools/skill_provider.py` |
| `internal/agent/skill_adapters.go` | LLM 审查 + 问用户 | `agent.py` 审查器 + TUI `SkillChoiceModal` |
| `internal/prompt/templates/system.md` | Available Skills 摘要 | `Agent._build_system_prompt` |

TUI `/skills` 对照 Codex SkillPopup：输入模糊搜索，Enter 插入 `$name`。
发送时 Skill 正文进入**系统提示词**（Active Skills），用户消息只保留真正的请求。
额外 Skill 用 `skill_load` 加载，不要 `list_dir` skills 目录。
没有 daemon，所以不走 `skill.list` / `skill.set` JSON-RPC，Agent 直接调 Runtime。

启用记录 Suna 写在 `config.toml [skills.<name>]`；这里写成 `db/skills.json`，语义相同：`enabled` + `reasons`。

---

## 三、一个 Skill 长什么样

一个目录，必须有 `SKILL.md`：

```markdown
---
name: code-review
description: Review source code for bugs...
---

# Code Review
（完整工作方法，只有 skill_load 之后模型才看得到）
```

规则（对齐 Suna）：

- 索引阶段只解析 frontmatter 的 `name` / `description`，正文再大也不进 Reload
- 没有 YAML 时回退到 H1 + 首段
- 名字只允许字母数字和 `-` `_` `.`，最长 80
- 可选 `scripts/`、`references/` 等资源；`scripts/` 会在静态检查里记一条 reason

预置了两个全局 Skill：

- `skills/code-review/`
- `skills/write-tests/`

---

## 四、加载流程

```
扫描全局 skills 根目录
    → 只读 frontmatter
    → records 里没有的新目录：默认 enabled（用户亲手放进去的，视为已信任）
    → 拼进 system prompt 的 Available Skills 摘要

模型决定需要完整方法
    → skill_load(name, scope=global)
    → 必须 enabled + valid
    → 把 SKILL.md 全文作为工具结果塞回对话

project Skill
    → 从 cwd 向上发现约定目录（每层只取优先级最高的一个根）：
      `.agents/skills` → `.codex/skills` → `.pi/skills` → `.claude/skills` →
      `.github/skills` → `.gemini/skills` → `.cursor/skills` → `.opencode/skills`
    → 会话开始时发现一次，之后不再扫盘
    → skill_load 必须带发现时的精确 path
    → 拒绝 symlink 根目录 / symlink SKILL.md

user Skill（其它 agent 主目录）
    → 首次启动按 name 去重，拷进本 agent 的 `skills/`（Codex 优先于 Grok）
    → 已有同名目录不覆盖；留下 `.imported-user-skills.json` 后下次不再扫描
    → 之后当全局 Skill 用；新技能用 `/skills sync` 再导入
```

`skill_start` 是另一条路，给「导入或刚放进目录、还不能直接用」的 Skill：

```
import 或 check
    → 静态 Check（不改 enabled 的合法性，只收集 reasons）
    → import / check 之后先 Disable
    → 问用户：要不要 LLM 审查
    → 问用户：要不要 Enable
    → 未启用的 Skill，skill_load 会失败
```

这是 Suna 最关键的边界：**目录里出现 ≠ 模型立刻能加载全文。** 手动丢进目录的默认启用；`import` / `check` 必须显式确认。

---

## 五、两个工具

### skill_load（Perceive，GuardNever）

```
name   精确 Skill 名
scope  global | project
path   仅 project 需要，必须是发现时的路径；global 必须省略
```

返回：

```
[Skill: code-review]
Scope: global
Skill root: /path/to/skills/code-review

---
name: code-review
...
```

### skill_start（Act，GuardNever）

```
action  import | check
name    check 必填；import 可空（用 SKILL.md 的 name）
source  本地目录 / zip / git URL（import 用）
```

返回给模型的是摘要 JSON（审查长文不会整篇塞回去）。

两个工具在 runner 里跳过 Guard，对应 Suna 的 `GuardNever`：启用校验由 Runtime 自己做，不走命令审查。

---

## 六、代码结构

```
myagent/skill/
├── metadata.py     读 frontmatter
├── manager.py      扫描、Load、Check
├── catalog.py      project 发现、RenderSummary
├── store.py        records 持久化（JSON / 内存）
├── runtime.py      给 Agent 用的门面
├── review.py       审查文件收集
├── workflow.py     skill_start 结果和问句
└── importer.py     本地 / zip / git 导入

myagent/tools/skill_provider.py   适配成 Tool
skills/code-review/SKILL.md
skills/write-tests/SKILL.md
```

Agent 接入：

1. 创建 `Runtime`，Reload 一次
2. `discover_project(cwd)` 冻结本会话的 project catalog
3. 把 `skill_load` / `skill_start` 并进工具列表
4. 每轮 `run()` 前按当前启用状态刷新系统提示词
5. TUI `on_mount` 注入 `set_skill_prompter`，`skill_start` 弹选项窗
6. TUI 输入 `/` 模糊补全 Skill；发送 `/name` 或 `$name` 把正文写入系统提示词 Active Skills
7. 首次启动把 `~/.codex/skills` 等按同名拷进 `skills/`，`/skills sync` 可再导入

配置：

```bash
MY_AGENT_SKILLS_DIR=skills
MY_AGENT_SKILLS_RECORDS=db/skills.json
MY_AGENT_SKILLS_USER_HOME=~          # 设为 - 则不导入其它 agent 的用户级 Skill
```

测试里不配 records 路径则用内存 Store，避免单测写脏 `db/`。

---

## 七、实现过程

1. 对照 Suna 源码，先把边界抄清楚：索引 ≠ 正文、global ≠ project、启用 ≠ 合法、Check 不在启动时跑。
2. 按 Suna 文件切分 Python 包，不把 Manager / Catalog / Runtime 揉成一个文件。
3. frontmatter 自己解析（name/description/`>` 折叠），不引入 PyYAML，行为对齐 `metadata.go` 的测试。
4. 工具层只做适配，和 MCP provider 同一套路。
5. 把 Suna 的 `skill_test.go` / `catalog_test.go` / `runtime_test.go` 关键用例迁过来，再加 Agent 接入和 GuardNever 回归。
6. TUI 只加 `skill_start` 必需的选项弹窗；后来补了 `/` 补全、Active Skills、主目录导入。

中途踩过的对齐点：

- Reload 不能读完整 `SKILL.md`，否则大 Skill 会把启动拖垮（Suna 用 64KB 窗口）
- `SetEnabled` 不重新扫盘，描述保持内存里的索引；`Load` 读的是磁盘当前正文
- 导入已安装路径、源和目标互相包含，直接拒绝
- project Skill 的 path 必须和 session catalog 里的完全一致，防模型瞎猜路径

---

## 八、验证结果

| 测试 | 结果 |
|------|------|
| 启用 Skill 进入摘要，未启用不能 Load | ✅ |
| 改正文不取消启用；Load 读到新正文 | ✅ |
| Reload 只用 frontmatter，正文再大也不进索引 | ✅ |
| frontmatter 字段顺序无关；折叠标量能拼 description | ✅ |
| 超大未闭合 frontmatter → invalid | ✅ |
| Check 能标出 curl/sudo/scripts | ✅ |
| 重名 Skill invalid | ✅ |
| project 发现：有 git 走祖先，无 git 只扫 cwd；同层根目录有优先级 | ✅ |
| symlink 根 / symlink SKILL.md 拒绝 | ✅ |
| 手动放入目录默认启用；import/check 必须用户确认 | ✅ |
| skill_load 返回全文；readonly Guard 也放行 | ✅ |
| Agent 系统提示词含 Available Skills | ✅ |
| `$name` / 行首 `/name` 激活进系统提示词，用户消息只留请求 | ✅ |
| 同名用户级 Skill 只拷一份；有 stamp 后启动不再扫主目录 | ✅ |
| `/` 补全模糊匹配，匹配字与输入框命令标蓝 | ✅ |

验收句：输入 `/using-superpowers 帮我构思` 或 `$ponytail 简化这段`，本轮系统提示词出现 Active Skills，用户消息不含 Skill 正文。

---

## 十、后续补上的体验与合同（2026-09-15）

这些是 Skill 落地之后、对照 Codex / Grok / Suna 补的，不是第一版范围。

**激活通道（对照 Codex skill input item）**

- 正文进系统提示词 `## Active Skills`，不当作用户请求
- 额外 Skill 用 `skill_load`，禁止 `list_dir` skills 目录来「调用」Skill
- 行首 `/using-superpowers` 与 `$ponytail` 等效（`/model` `/skills` `/quit` 除外）

**TUI（对照 Grok `/su` 补全）**

- 输入 `/` 弹出命令 + Skill 模糊列表，匹配字母蓝色加粗
- 上下键选择，Tab/Enter 补全；完整 `/skills` 再 Enter 才执行命令
- 输入框和用户气泡里的 `/name`、`$name` 标蓝

**Suna 工具边界**

- `run_command` 默认 60s，超时当工具结果返回，不崩循环
- `http` timeout 为整数秒，默认 UA；`"8000"` 按毫秒收成 8s
- 描述写明：能用 http/search/文件就不要用 shell；搜 skills.sh 用 `/api/search?q=`
- 8 轮工具用尽后关掉 tools 再问一次，强制收口；`add_assistant(None)` 存成 `""`

**Suna 第一句合同**

system prompt 开头是：完成用户任务；失败先看原因再换方法。仓库没有命中就 `http` 查文档，不要空泛提示。

---

## 十一、一句话总结

**Skill 是按需加载的工作方法：短描述常驻，用户用 `/` 或 `$` 激活后正文进系统提示词，模型用 `skill_load` 再加载其它 Skill。**
