"""工具"执行路由"。

这里的职责是：把所有内置工具收集起来，并维护两份映射：
  - schemas：给模型（它只需要"声明"）
  - funcs  ：我们执行（按名字找到对应函数来调用）

对应 Suna 的 internal/tools + internal/tools/builtin。
"""
from .base import Tool


def _read_file(path: str) -> str:
    """读取一个文本文件并返回其内容。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except IsADirectoryError:
        return f"这是一个目录: {path}"


def _list_dir(path: str) -> str:
    """列出一个目录下的内容，让模型可以"看"项目结构。"""
    import os
    try:
        return "\n".join(sorted(os.listdir(path)))
    except FileNotFoundError:
        return f"目录不存在: {path}"
    except Exception as exc:
        return f"列出目录失败: {exc}"


def _run_command(command: str) -> str:
    """在本地执行一条 shell 命令并返回输出。

    注意：这个工具是"命令执行能力"的入口，也是最危险的权力来源。
    真实工程里（参考 Suna 的 Guard）必须对它做安全审查 / 用户确认。
    这里只做最基础的超时保护，安全话题见 docs/架构与选型.md。
    """
    import subprocess
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,          # 防止命令跑死
    )
    output = (result.stdout or "") 
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    if result.returncode != 0:
        output += f"\n[exit code] {result.returncode}"
    return output


# 内置工具清单：每项 = (名字, 描述, 参数名列表, 必填参数, 执行函数)
# 注意：下面 _REGISTRY 里放的是一个个 Tool 对象（不是模块）。
_REGISTRY = [
    Tool.make(
        name="read_file",
        description="读取指定路径的文本文件内容。当需要查看某个文件的代码或配置时使用。",
        param_names=["path"],
        required=["path"],
    ).bind(_read_file),
    Tool.make(
        name="list_dir",
        description="列出某个目录下的条目的名字。当需要了解项目结构时使用。",
        param_names=["path"],
        required=["path"],
    ).bind(_list_dir),
    Tool.make(
        name="run_command",
        description="在本地执行一条 shell 命令并返回其输出。当需要运行程序或查看系统信息时使用。",
        param_names=["command"],
        required=["command"],
    ).bind(_run_command),
]

# 对外只暴露这两个列表，别的模块不需要知道"内置工具"这个细节
# TOOLS 是 Tool 对象的列表；SCHEMAS 是把每个 Tool 的 .schema（一个 dict）提出来组成新列表。
# 这里的 tool 变量 = TOOLS 里的元素（一个 Tool 对象），不是 tools 包。
TOOLS: list = _REGISTRY
SCHEMAS: list = [tool.schema for tool in TOOLS]


def tool_for(name: str):
    """按名字找到可执行工具，找不到返回 None（让 runner 优雅处理）。"""
    for tool in TOOLS:
        if tool.name == name:
            return tool
    return None
