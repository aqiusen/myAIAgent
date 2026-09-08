"""Textual 聊天 TUI 界面。

对应 Suna 的 TUI（Bubble Tea）。用 Textual（Python 的 Bubble Tea 对应物）
做出聊天软件风格的界面：用户消息靠左、Agent 消息靠右、工具调用居中。

架构：
  - App 持有 Agent，负责渲染与交互。
  - 用户提交 → 起一个后台 worker 线程跑 agent.run()（同步阻塞，放线程里不卡 UI）。
  - on_delta 回调通过 call_from_thread 安全地更新 UI（流式输出）。
  - on_tool_call 回调把工具调用显示为居中的系统消息。
  - Guard 的 ask 模式确认：默认拒绝（fail-closed），避免线程交互复杂度。
"""
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal
from textual.widgets import Header, Footer, Input, Static
from textual import work

from .agent import Agent


class ChatApp(App):
    """myAIAgent 的聊天界面。"""

    TITLE = "myAIAgent"
    SUB_TITLE = "本地代码 Agent"

    CSS = """
    #chat {
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #input {
        dock: bottom;
        margin: 1 2;
    }
    /* 消息行：整行宽度，气泡靠左/靠右 */
    .msg-row {
        width: 100%;
        margin-bottom: 1;
    }
    .user-row  { align-horizontal: left; }
    .agent-row { align-horizontal: right; }
    .tool-row  { align-horizontal: center; }

    /* 气泡 */
    .bubble {
        padding: 1 2;
        max-width: 80%;
        border: round $primary;
    }
    .user-row .bubble {
        background: $primary;
        color: $text;
    }
    .agent-row .bubble {
        background: $surface-lighten-1;
    }
    .tool-row .bubble {
        background: transparent;
        border: none;
        color: $warning;
        text-style: italic;
    }
    .system-row .bubble {
        background: transparent;
        border: none;
        color: $success;
        text-style: bold;
    }
    """

    def __init__(self, agent: Agent):
        super().__init__()
        self.agent = agent
        self._stream = None          # 当前流式输出的气泡 Static
        self._stream_text = ""       # 流式输出累积文本

    # ---------- 界面搭建 ----------
    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield Input(id="input", placeholder="输入你的问题，/quit 退出")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input").focus()
        self._add_message("myAIAgent", "已启动。输入你的问题，输入 /quit 退出。", "system-row")

    # ---------- 消息渲染 ----------
    def _add_message(self, sender: str, text: str, row_cls: str) -> None:
        """添加一条消息：sender 标签 + 气泡，按 row_cls 靠左/靠右/居中。"""
        chat = self.query_one("#chat")
        bubble = Static(f"[bold]{sender}[/] {text}", classes="bubble")
        row = Horizontal(bubble, classes=f"msg-row {row_cls}")
        chat.mount(row)
        chat.scroll_end(animate=False)

    def _add_tool_call(self, name: str, args: str) -> None:
        """工具调用：居中显示为系统消息。"""
        self._add_message("工具", f"{name}({args})", "tool-row")

    def _append_stream(self, delta: str) -> None:
        """流式输出：把增量追加到当前 Agent 气泡上。"""
        if self._stream is None:
            return
        self._stream_text += delta
        self._stream.update(f"[bold]Agent[/] {self._stream_text}")
        self.query_one("#chat").scroll_end(animate=False)

    def _finish_stream(self) -> None:
        self._stream = None
        self._stream_text = ""

    # ---------- 交互 ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text == "/quit":
            self.exit()
            return
        self.query_one("#input").clear()
        self._add_message("你", text, "user-row")

        # 创建 Agent 流式气泡（靠右）
        self._stream = Static("", classes="bubble")
        row = Horizontal(self._stream, classes="msg-row agent-row")
        self.query_one("#chat").mount(row)
        self.query_one("#chat").scroll_end(animate=False)
        self._stream_text = ""

        # 后台线程跑 agent，不卡 UI（@work(thread=True) 会自动启动 worker）
        self._run_agent(text)

    @work(thread=True)
    def _run_agent(self, text: str) -> None:
        def on_delta(delta: str) -> None:
            self.call_from_thread(self._append_stream, delta)

        def on_tool_call(name: str, args: str) -> None:
            self.call_from_thread(self._add_tool_call, name, args)

        try:
            self.agent.run(text, on_delta=on_delta, on_tool_call=on_tool_call)
        except Exception as exc:
            self.call_from_thread(self._append_stream, f"\n[red]错误: {exc}[/]")
        finally:
            self.call_from_thread(self._finish_stream)


def run_tui(agent: Agent) -> None:
    """启动 Textual 聊天界面。"""
    ChatApp(agent).run()
