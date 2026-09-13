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
import re
from typing import Optional

from rich.cells import cell_len
from rich.markdown import Markdown
from rich.measure import Measurement
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Header, Footer, Input, Label, Static

from .agent import Agent


LINK_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)|\[([^\]]+)\]\([^)]+\)")
INLINE_MARK_RE = re.compile(r"(\*\*|__|\*|_|`|~~)")
LIST_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")


class BubbleMarkdown:
    """Rich Markdown renderable with content-sized layout measurement."""

    def __init__(self, text: str):
        self.text = text
        self.markdown = Markdown(text, justify="left")

    def __rich_console__(self, console, options):
        yield from self.markdown.__rich_console__(console, options)

    def __rich_measure__(self, console, options):
        width = min(_markdown_content_width(self.text), options.max_width)
        return Measurement(width, width)


def _markdown_content_width(text: str) -> int:
    widths = [cell_len(_plain_markdown_line(line)) for line in text.splitlines()]
    return max(widths or [1], default=1)


def _plain_markdown_line(line: str) -> str:
    line = line.strip()
    line = line.lstrip("#> ")
    line = LIST_MARK_RE.sub("", line)
    line = LINK_RE.sub(lambda m: m.group(1) or m.group(2) or "", line)
    return INLINE_MARK_RE.sub("", line)


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


class ModelModal(ModalScreen[Optional[str]]):
    """选择运行时模型的弹窗。"""

    CSS = """
    ModelModal {
        align: center middle;
    }
    #model-panel {
        width: 44;
        height: auto;
        max-height: 18;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #model-title {
        width: 100%;
        height: 1;
        margin-bottom: 1;
        text-style: bold;
        color: $primary;
    }
    #model-current {
        width: 100%;
        height: 1;
        margin-bottom: 1;
        color: $text-muted;
    }
    .model-choice {
        width: 100%;
        margin-bottom: 1;
    }
    .current-model {
        border: round $success;
    }
    #model-cancel {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "dismiss")]

    def __init__(self, models: list[str], current: str):
        super().__init__()
        self.models = models
        self.current = current
        self._selected_index = models.index(current) if current in models else 0

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("切换模型", id="model-title"),
            Label(f"当前模型：{self.current}", id="model-current"),
            *(
                Button(
                    f"{ref}{'  (当前)' if ref == self.current else ''}",
                    id=f"model-choice-{index}",
                    classes="model-choice current-model" if ref == self.current else "model-choice",
                )
                for index, ref in enumerate(self.models)
            ),
            Button("取消", id="model-cancel"),
            id="model-panel",
        )

    def on_mount(self) -> None:
        self._focus_selected()

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down"} or not self.models:
            return
        event.stop()
        event.prevent_default()
        self._selected_index = (
            self._selected_index + (-1 if event.key == "up" else 1)
        ) % len(self.models)
        self._focus_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "model-cancel":
            self.dismiss(None)
            return
        index = int(event.button.id.removeprefix("model-choice-"))
        self.dismiss(self.models[index])

    def _focus_selected(self) -> None:
        self.query_one(f"#model-choice-{self._selected_index}", Button).focus()


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
        max-width: 88%;
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
        self._input_history = self._loaded_user_inputs()
        self._history_index: Optional[int] = None
        self._history_draft = ""

    # ---------- 界面搭建 ----------
    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield Input(id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input").focus()
        self._add_message(
            "myAIAgent",
            "已启动。输入 /model 切换模型，/setting 设置昵称，/q 或 /quit 退出。",
            "system-row",
        )
        # 若恢复了历史会话，把已加载的上下文渲染出来
        self._render_history()

    def _render_history(self) -> None:
        """启动时把 agent 内存里已加载的历史消息渲染到聊天区。"""
        msgs = getattr(self.agent, "memory", None)
        msgs = getattr(msgs, "_messages", None) if msgs else None
        if not msgs:
            return
        for m in msgs:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                continue  # 系统提示词是内部配置，不显示
            if role == "user":
                self._add_message(self.nickname, content, "user-row")
            elif role == "assistant":
                self._add_message("AI", content, "agent-row")
            elif role == "tool":
                # 工具结果作为工具消息居中显示（截断长内容）
                short = content if len(content) <= 80 else content[:77] + "..."
                self._add_message("工具", short, "tool-row")

    def _loaded_user_inputs(self) -> list[str]:
        msgs = getattr(self.agent, "memory", None)
        msgs = getattr(msgs, "_messages", None) if msgs else None
        if not msgs:
            return []
        return [
            m.get("content", "")
            for m in msgs
            if m.get("role") == "user" and m.get("content")
        ]

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
            bubble = Static(self._chat_renderable(text, row_cls), classes="bubble")
            avatar_text = self.nickname if row_cls == "user-row" else "AI"
            avatar = Static(avatar_text, classes="avatar")
            if row_cls == "user-row":
                return Horizontal(bubble, avatar, classes=f"msg-row {row_cls}"), bubble
            return Horizontal(avatar, bubble, classes=f"msg-row {row_cls}"), bubble

        bubble = Static(f"[bold]{sender}:[/] {text}", classes="bubble")
        return Horizontal(bubble, classes=f"msg-row {row_cls}"), bubble

    @staticmethod
    def _chat_renderable(text: str, row_cls: str):
        return BubbleMarkdown(text) if row_cls == "agent-row" else text

    def _add_tool_call(self, name: str, args: str) -> None:
        """工具调用：居中显示为系统消息。"""
        self._add_message("工具", f"{name}({args})", "tool-row")

    def _append_stream(self, delta: str) -> None:
        """流式输出：把增量追加到当前 Agent 气泡上。"""
        if self._stream is None:
            return
        self._stream_text += delta
        self._stream.update(self._chat_renderable(self._stream_text, "agent-row"))
        self.query_one("#chat").scroll_end(animate=False)

    def _finish_stream(self) -> None:
        self._stream = None
        self._stream_text = ""

    # ---------- 交互 ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # ModalScreen 里的 Input.Submitted 也会冒泡到 App；只处理聊天主输入框。
        if event.input.id != "input":
            return
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
        if text == "/model":
            self.query_one("#input").clear()
            self._open_model_modal()
            return
        if text.startswith("/model "):
            self.query_one("#input").clear()
            self._switch_model(text.removeprefix("/model ").strip())
            return
        self.query_one("#input").clear()
        self._remember_input(text)
        self._add_message("你", text, "user-row")

        # 创建 Agent 流式气泡（靠左）
        row, self._stream = self._build_message_row("Agent", "", "agent-row")
        self.query_one("#chat").mount(row)
        self.query_one("#chat").scroll_end(animate=False)
        self._stream_text = ""

        # 后台线程跑 agent，不卡 UI（@work(thread=True) 会自动启动 worker）
        self._run_agent(text)

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down"}:
            return
        input_box = self.query_one("#input", Input)
        if not input_box.has_focus:
            return
        event.stop()
        event.prevent_default()
        self._move_input_history(-1 if event.key == "up" else 1)

    def _remember_input(self, text: str) -> None:
        if not self._input_history or self._input_history[-1] != text:
            self._input_history.append(text)
        self._history_index = None
        self._history_draft = ""

    def _move_input_history(self, step: int) -> None:
        if not self._input_history:
            return
        input_box = self.query_one("#input", Input)
        if self._history_index is None:
            self._history_draft = input_box.value
            self._history_index = len(self._input_history) - 1
        else:
            self._history_index += step

        if self._history_index < 0:
            self._history_index = 0
        if self._history_index >= len(self._input_history):
            self._history_index = None
            value = self._history_draft
        else:
            value = self._input_history[self._history_index]
        input_box.value = value
        input_box.cursor_position = len(value)

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

    def _open_model_modal(self) -> None:
        """打开模型选择器。"""
        self.push_screen(
            ModelModal(self.agent.list_models(), self.agent.current_model()),
            self._on_model_modal,
        )

    def _on_model_modal(self, ref: Optional[str]) -> None:
        if ref is not None:
            self._switch_model(ref)
        self.query_one("#input").focus()

    def _switch_model(self, ref: str) -> None:
        """切换到指定模型。"""
        try:
            self.agent.switch_model(ref)
            self._add_message("系统", f"已切换到模型: {ref}", "system-row")
        except KeyError as exc:
            self._add_message("系统", str(exc), "system-row")

    @work(thread=True)
    def _run_agent(self, text: str) -> None:
        def on_delta(delta: str) -> None:
            self.call_from_thread(self._append_stream, delta)

        def on_tool_call(name: str, args: str) -> None:
            self.call_from_thread(self._add_tool_call, name, args)

        try:
            self.agent.run(text, on_delta=on_delta, on_tool_call=on_tool_call)
        except Exception as exc:
            self.call_from_thread(self._append_stream, f"\n**错误:** {exc}")
        finally:
            self.call_from_thread(self._finish_stream)


def run_tui(agent: Agent) -> None:
    """启动 Textual 聊天界面。"""
    ChatApp(agent).run()
