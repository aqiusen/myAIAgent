"""命令行交互入口。

对应 Suna 的 TUI：负责跟用户打交道 + 打印渲染，不装任何业务语义。

阶段 1：最简版 REPL，用 input() 读输入。
阶段 2：改用 prompt_toolkit 读输入，解决编码问题。
阶段 3：改用 Textual 做完整 TUI（聊天界面 + 流式输出 + 工具展示）。
（为什么升级，见 docs/prompt_toolkit使用原因.md 与 docs/Textual界面.md）
"""
import sys

from .config import Config
from .agent import Agent
from .tui import run_tui


def main() -> None:
    # 输出统一用 UTF-8，避免打印中文时报错。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    # 1) 加载配置（失败直接退出，给用户清晰报错）
    try:
        config = Config.from_env()
    except RuntimeError as exc:
        print(exc)
        return

    # 2) 组装 agent
    # ask 模式下 Guard 需要用户确认。TUI 里默认拒绝（fail-closed），
    # 避免线程交互复杂度；默认 smart 模式用 LLM 审查，不依赖用户确认。
    agent = Agent(config, confirm_callback=lambda params: False)

    # 3) 启动 Textual 聊天界面
    run_tui(agent)
