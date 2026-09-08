"""命令行交互入口。

对应 Suna 的 TUI：负责跟用户打交道 + 打印渲染，不装任何业务语义。

阶段 1 是最简版：一个 while True 的 REPL，用 input() 读输入。
阶段 2 升级：改用 prompt_toolkit 读输入，解决终端编码不匹配问题，
并顺带获得历史记录、多行输入、IME 支持等能力。
（为什么替换，见 docs/prompt_toolkit使用原因.md）
"""
from .config import Config
from .agent import Agent
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory


def main() -> None:
    # 输出统一用 UTF-8，避免打印中文时报错。
    # 输入不再手动处理编码——prompt_toolkit 读原始字节，从架构上绕开编码问题。
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
    # prompt_toolkit 的 PromptSession：读原始字节 + 自带历史记录。
    # 先建 session，供 confirm_callback 复用（ask 模式询问用户）。
    session = PromptSession(history=InMemoryHistory())

    # confirm_callback：ask 模式下，Guard 遇到风险操作时询问用户是否放行。
    def confirm_callback(params) -> bool:
        command = params.get("command", "")
        try:
            answer = session.prompt(f"\n[Guard] 是否允许执行该命令？(y/N) {command}\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    agent = Agent(config, confirm_callback=confirm_callback)

    # 3) 招呼一下
    print("my-agent 已启动。输入你的问题，输入 /quit 退出。\n")

    # 4) 对话循环
    while True:
        try:
            user_input = session.prompt("牛逼大森哥:> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break

        if not user_input:
            continue
        if user_input.strip() == "/quit":
            print("再见")
            break

        # 单轮：流式输出，边生成边打印（阶段 1.2）
        # on_delta 回调负责把每个文字片段实时打出来（不换行、立即刷新）。
        print("\nAgent> ", end="", flush=True)
        answer = agent.run(
            user_input,
            on_delta=lambda text: print(text, end="", flush=True),
        )
        print()  # 流式结束后补一个换行
        print("-" * 40)
