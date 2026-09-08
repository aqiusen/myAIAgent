# prompt_toolkit 使用原因

> 记录：为什么把输入层从 `input()` 替换成 `prompt_toolkit`。
> 目的、遇到的问题、替换后的好处，一次说清。

---

## 一、我碰到了什么问题

### 问题：中文输入反复报编码错误

在 IDEA 控制台里运行 `main.py`，输入中文时反复出现两类错误：

**错误 1：`UnicodeEncodeError`（代理字符）**

```
UnicodeEncodeError: 'utf-8' codec can't encode character '\udce4'
in position 310: surrogates not allowed
```

**错误 2：`UnicodeDecodeError`（解码失败）**

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe6
in position 6: invalid continuation byte
```

### 根因：Python `input()` 的"文本模式 stdin"编码不匹配

Python 的 `input()` 走的是**文本模式 stdin**，它有一个**预先配置好的编码**
（从 locale / 终端设置读取）。当终端实际发送的字节和这个配置不一致时：

- 不强制 UTF-8 → 产生代理字符（surrogate）→ 发 API 时编码失败
- 强制 UTF-8 → 直接解码失败

**本质**：`input()` 依赖"环境编码配置正确"，而 IDEA 控制台的编码没配好，
导致反复踩坑。

---

## 二、我尝试过的方案（以及为什么放弃）

### 方案 1：强制 stdin 用 UTF-8

```python
sys.stdin.reconfigure(encoding="utf-8")
```

- ❌ 治标不治本：环境发非 UTF-8 字节时，从"代理字符"变成"解码失败"，换个姿势报错

### 方案 2：自定义 `_read_line`，读原始字节 + 多编码回退

```python
raw = sys.stdin.buffer.readline()
for enc in ("utf-8", "gbk", "big5", ...):
    return raw.decode(enc)
```

- ✅ 能解决编码问题
- ❌ 但只是"猜编码"的补丁，且只解决"读一行"
- ❌ 没有历史记录、多行输入、IME 支持、自动补全等成品需要的能力

### 结论

自定义方案对**学习原型**够用，但对**成品 agent** 是"自己造轮子"。
输入层应该交给成熟库，而不是自己处理底层编码。

---

## 三、为什么替换成 prompt_toolkit

### 1. 从架构上绕开编码问题

`prompt_toolkit` 和 Suna 用的 Bubble Tea 一个思路：**读原始字节，直接按 UTF-8 解码**，
不存在"文本模式 stdin 配置编码"这回事。编码不匹配问题从根上消失。

### 2. 成品 agent 需要的输入能力，开箱即用

| 能力 | `input()` | 自定义 `_read_line` | `prompt_toolkit` |
|------|-----------|---------------------|------------------|
| 编码健壮 | ❌ | ⚠️ 猜编码 | ✅ 读原始字节 |
| 历史记录 | ❌ | ❌ | ✅ |
| 多行输入 | ❌ | ❌ | ✅ |
| IME 输入法 | ⚠️ | ⚠️ | ✅ |
| 自动补全 | ❌ | ❌ | ✅ |
| 粘贴大段文本 | ⚠️ | ⚠️ | ✅ |

### 3. 成熟、稳定、社区认可

- `prompt_toolkit` 是 Python 交互式 CLI 的**事实标准**
- 被大量生产项目使用（IPython、pgcli、httpie 等）
- 持续维护，文档完善

---

## 四、替换之后的好处

### 代码更简单

替换前（自定义 `_read_line`，几十行处理编码）：

```python
def _read_line(prompt):
    sys.stdout.write(prompt); sys.stdout.flush()
    raw = sys.stdin.buffer.readline()
    for enc in ("utf-8", "gbk", "big5", sys.getfilesystemencoding()):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
```

替换后（一行）：

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

session = PromptSession(history=InMemoryHistory())
user_input = session.prompt("牛逼大森哥:> ")
```

### 能力更强

- 上下键翻历史记录
- 支持多行输入（贴代码）
- 输入法组合正常
- 未来可加自动补全、语法高亮

### 更接近 Suna 的架构

Suna 用 Bubble Tea 做 TUI，`prompt_toolkit` 是 Python 生态里对应的选择。
输入层交给成熟库，业务逻辑（agent 循环）保持独立，符合分层架构。

---

## 五、代价与权衡

- **新增一个依赖**：`prompt_toolkit`（含 `wcwidth`）
- 对"依赖极简"的学习项目是取舍，但对**成品 agent 是值得的**——输入层不该自己造轮子

---

## 六、一句话总结

**`input()` 依赖环境编码配置，反复踩坑；自定义方案是治标补丁；
`prompt_toolkit` 读原始字节、从架构上绕开编码问题，并自带成品需要的
历史/多行/IME 能力，是生产级 agent 输入层的正确选择。**
