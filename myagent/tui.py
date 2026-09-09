"""Textual 聊天 TUI 界面。

对应 Suna 的 TUI（Bubble Tea）。用 Textual（Python 的 Bubble Tea 对应物）
做出聊天软件风格的界面：Agent 消息靠左、用户消息靠右、工具调用居中。

架构：
  - App 持有 Agent，负责渲染与交互。
  - 用户提交 → 起一个后台 worker 线程跑 agent.run()（同步阻塞，放线程里不卡 UI）。
  - on_delta 回调通过 call_from_thread 安全地更新 UI（流式输出）。
  - on_tool_call 回调把工具调用显示为居中的系统消息。
  - Guard 的 ask 模式确认：默认拒绝（fail-closed），避免线程交互复杂度。
"""
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Header, Footer, Input, Label, Static
from textual import work

from .agent import Agent


class NicknameModal(ModalScreen[Optional[str]]):
    """设置用户昵称的弹窗。"""

    CSS = """
    NicknameModal {
        align: center middle;
    }
    #setting-panel {
        width: 44;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #setting-title {
        width: 100%;
        height: 1;
        margin-bottom: 1;
        text-style: bold;
        color: $warning;
    }
    #setting-name {
        width: 100%;
        height: 3;
        margin-bottom: 1;
    }
    #setting-actions {
        width: 100%;
        height: auto;
        align-horizontal: right;
    }
    #setting-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS = [("escape", "dismiss")]

    def __init__(self, current_name: str):
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("设置昵称", id="setting-title"),
            Input(value=self.current_name, id="setting-name"),
            Horizontal(
                Button("取消", id="cancel"),
                Button("保存", id="save", variant="warning"),
                id="setting-actions",
            ),
            id="setting-panel",
        )

    def on_mount(self) -> None:
        self.query_one("#setting-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss(self.query_one("#setting-name", Input).value.strip())


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
        padding: 1 1;
        height: 3;
        border: none;
        background: $surface;
    }
    #input:focus {
        border: none;
        background: $surface;
    }
    #input > .input--cursor {
        background: $surface;
        color: $text;
    }
    #input > .input--selection {
        background: $surface-lighten-1;
        color: $text;
    }
    /* 消息行：整行宽度，气泡靠左/靠右 */
    .msg-row {
        width: 100%;
        height: auto;
        align-vertical: middle;
        margin-bottom: 1;
    }
    .user-row  { align-horizontal: right; }
    .agent-row { align-horizontal: left; }
    .tool-row  { align-horizontal: center; }

    .avatar {
        width: auto;
        max-width: 12;
        height: 100%;
        margin: 0 1;
        content-align: center middle;
        background: transparent;
        color: $text-muted;
    }
    .user-row .avatar {
        color: $warning;
    }

    /* 气泡 */
    .bubble {
        padding: 0 1;
        width: auto;
        height: auto;
        max-width: 100%;
        border: round $primary;
    }
    .user-row .bubble {
        background: transparent;
        border: round $warning;
        color: $warning;
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
        self.nickname = "我"          # 用户消息右侧的迷你标识

    # ---------- 界面搭建 ----------
    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield Input(id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input").focus()
        self._add_message("myAIAgent", "已启动。输入 /setting 设置昵称，输入 /q 或 /quit 退出。", "system-row")

    # ---------- 消息渲染 ----------
    def _add_message(self, sender: str, text: str, row_cls: str) -> None:
        """添加一条消息：sender 标签 + 气泡，按 row_cls 靠左/靠右/居中。"""
        chat = self.query_one("#chat")
        row, _bubble = self._build_message_row(sender, text, row_cls)
        chat.mount(row)
        chat.scroll_end(animate=False)

    def _build_message_row(self, sender: str, text: str, row_cls: str):
        """构建一行消息。聊天消息只显示气泡，系统消息仍保持简洁居中。"""
        if row_cls in {"user-row", "agent-row"}:
            bubble = Static(text, classes="bubble")
            avatar_text = self.nickname if row_cls == "user-row" else "AI"
            avatar = Static(avatar_text, classes="avatar")
            if row_cls == "user-row":
                return Horizontal(bubble, avatar, classes=f"msg-row {row_cls}"), bubble
            return Horizontal(avatar, bubble, classes=f"msg-row {row_cls}"), bubble

        bubble = Static(f"[bold]{sender}:[/] {text}", classes="bubble")
        return Horizontal(bubble, classes=f"msg-row {row_cls}"), bubble

    def _add_tool_call(self, name: str, args: str) -> None:
        """工具调用：居中显示为系统消息。"""
        self._add_message("工具", f"{name}({args})", "tool-row")

    def _append_stream(self, delta: str) -> None:
        """流式输出：把增量追加到当前 Agent 气泡上。"""
        if self._stream is None:
            return
        self._stream_text += delta
        self._stream.update(self._stream_text)
        self.query_one("#chat").scroll_end(animate=False)

    def _finish_stream(self) -> None:
        self._stream = None
        self._stream_text = ""

    # ---------- 交互 ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text in {"/q", "/quit"}:
            self.exit()
            return
        if text == "/setting":
            self.query_one("#input").clear()
            self.push_screen(NicknameModal(self.nickname), self._on_nickname_modal)
            return
        if text.startswith("/setting "):
            self.query_one("#input").clear()
            self._set_nickname(text.removeprefix("/setting ").strip())
            return
        self.query_one("#input").clear()
        self._add_message("你", text, "user-row")

        # 创建 Agent 流式气泡（靠左）
        row, self._stream = self._build_message_row("Agent", "", "agent-row")
        self.query_one("#chat").mount(row)
        self.query_one("#chat").scroll_end(animate=False)
        self._stream_text = ""

        # 后台线程跑 agent，不卡 UI（@work(thread=True) 会自动启动 worker）
        self._run_agent(text)

    def _on_nickname_modal(self, nickname: Optional[str]) -> None:
        if nickname is not None:
            self._set_nickname(nickname)
        self.query_one("#input").focus()

    def _set_nickname(self, nickname: str) -> None:
        if not nickname:
            self._add_message("系统", "昵称不能为空。", "system-row")
            return
        self.nickname = nickname
        self._add_message("系统", f"昵称已设置为：{nickname}", "system-row")

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
