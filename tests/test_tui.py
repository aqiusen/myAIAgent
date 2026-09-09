"""TUI 交互测试。"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rich.console import Console
from textual.widgets import Input

from myagent.tui import BubbleMarkdown, ChatApp


def test_agent_markdown_keeps_content_sized_measurement():
    renderable = ChatApp._chat_renderable("这里 **会加粗** 结束", "agent-row")

    assert isinstance(renderable, BubbleMarkdown)
    console = Console(width=120)
    measure = renderable.__rich_measure__(console, console.options)
    assert measure.maximum < 120


def test_agent_markdown_supports_full_markdown_blocks():
    renderable = ChatApp._chat_renderable("- **第一项**\n- `第二项`", "agent-row")

    assert isinstance(renderable, BubbleMarkdown)


def test_input_history_uses_loaded_user_messages():
    async def run():
        agent = SimpleNamespace(
            memory=SimpleNamespace(
                _messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "第一句"},
                    {"role": "assistant", "content": "回复"},
                    {"role": "user", "content": "第二句"},
                ]
            )
        )
        app = ChatApp(agent)
        async with app.run_test() as pilot:
            input_box = app.query_one("#input", Input)
            await pilot.press("up")
            assert input_box.value == "第二句"
            await pilot.press("up")
            assert input_box.value == "第一句"
            await pilot.press("down")
            assert input_box.value == "第二句"
            await pilot.press("down")
            assert input_box.value == ""

    asyncio.run(run())
