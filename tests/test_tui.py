"""TUI 交互测试。"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rich.console import Console
from textual.widgets import Input

from myagent.tui import BubbleMarkdown, ChatApp, ModelModal, NicknameModal


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


def test_model_modal_switches_with_keyboard():
    async def run():
        modal = ModelModal(["default", "vision"], "default")
        app = ChatApp(SimpleNamespace(memory=SimpleNamespace(_messages=[])))
        async with app.run_test() as pilot:
            app.push_screen(modal)
            await pilot.pause()
            assert modal.focused.id == "model-choice-0"
            await pilot.press("down")
            assert modal.focused.id == "model-choice-1"

    asyncio.run(run())


def test_model_modal_result_switches_agent():
    async def run():
        switched = []
        agent = SimpleNamespace(
            memory=SimpleNamespace(_messages=[]),
            switch_model=switched.append,
        )
        app = ChatApp(agent)
        async with app.run_test():
            app._on_model_modal("vision")
            assert switched == ["vision"]

    asyncio.run(run())


def test_nickname_modal_submit_is_not_sent_as_chat_message():
    async def run():
        sent = []
        agent = SimpleNamespace(
            memory=SimpleNamespace(_messages=[]),
            run=lambda text, **kwargs: sent.append(text) or "",
        )
        app = ChatApp(agent)
        async with app.run_test() as pilot:
            app.push_screen(NicknameModal("我"))
            await pilot.pause()
            name_input = app.screen.query_one("#setting-name", Input)
            name_input.value = "介绍下你自己"
            await pilot.press("enter")
            await pilot.pause()
            assert sent == []

    asyncio.run(run())
