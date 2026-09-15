"""TUI 交互测试。"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rich.console import Console
from textual.widgets import Input, OptionList

from myagent.tui import BubbleMarkdown, ChatApp, ModelModal, NicknameModal, SkillsModal


def test_busy_status_shows_running_and_waiting():
    async def run():
        app = ChatApp(SimpleNamespace(memory=SimpleNamespace(_messages=[])))
        async with app.run_test():
            app._on_tool_start("run_command", '{"command":"npx skills find"}')
            assert "run_command" in app.sub_title
            assert "正在执行" in app.sub_title
            assert app._busy_widget is not None
            app._on_tool_done("run_command")
            assert "等待模型" in app.sub_title
            app._clear_busy()
            assert app.sub_title == "本地代码 Agent"
            assert app._busy_widget is None

    asyncio.run(run())


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


def test_skills_modal_lists_all_items():
    async def run():
        skills = [
            {
                "name": f"skill-{i}",
                "description": "desc",
                "enabled": True,
                "valid": True,
                "can_toggle": True,
                "scope": "global",
            }
            for i in range(10)
        ]
        modal = SkillsModal(skills)
        app = ChatApp(SimpleNamespace(memory=SimpleNamespace(_messages=[])))
        async with app.run_test() as pilot:
            app.push_screen(modal)
            await pilot.pause()
            assert modal.query_one("#skills-list", OptionList).option_count == 10

    asyncio.run(run())


def test_skills_modal_navigates_with_keyboard():
    async def run():
        modal = SkillsModal([
            {"name": "code-review", "description": "Review", "enabled": True, "valid": True, "can_toggle": True, "scope": "global"},
            {"name": "write-tests", "description": "Tests", "enabled": False, "valid": True, "can_toggle": True, "scope": "global"},
        ])
        app = ChatApp(SimpleNamespace(memory=SimpleNamespace(_messages=[])))
        async with app.run_test() as pilot:
            app.push_screen(modal)
            await pilot.pause()
            options = modal.query_one("#skills-list", OptionList)
            assert options.highlighted == 0
            await pilot.press("down")
            assert options.highlighted == 1

    asyncio.run(run())


def test_skills_command_is_not_sent_as_chat_message():
    async def run():
        sent = []
        agent = SimpleNamespace(
            memory=SimpleNamespace(_messages=[]),
            run=lambda text, **kwargs: sent.append(text) or "",
            list_skill_infos=lambda: [],
        )
        app = ChatApp(agent)
        async with app.run_test() as pilot:
            input_box = app.query_one("#input", Input)
            input_box.value = "/skills"
            await pilot.press("enter")
            await pilot.pause()
            assert sent == []
            assert isinstance(app.screen, SkillsModal)

    asyncio.run(run())


def test_skills_modal_enter_inserts_mention():
    async def run():
        agent = SimpleNamespace(
            memory=SimpleNamespace(_messages=[]),
            list_skill_infos=lambda: [
                {"name": "ponytail", "description": "Lazy", "enabled": True, "valid": True, "scope": "global"},
            ],
        )
        app = ChatApp(agent)
        async with app.run_test() as pilot:
            app._open_skills_modal()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.query_one("#input", Input).value == "$ponytail "

    asyncio.run(run())


def test_skills_modal_filters_by_query():
    async def run():
        modal = SkillsModal(
            [
                {"name": "ponytail", "description": "Lazy", "valid": True},
                {"name": "code-review", "description": "Review", "valid": True},
            ],
            query="pony",
        )
        app = ChatApp(SimpleNamespace(memory=SimpleNamespace(_messages=[])))
        async with app.run_test() as pilot:
            app.push_screen(modal)
            await pilot.pause()
            assert modal.query_one("#skills-list", OptionList).option_count == 1
            assert modal._visible[0]["name"] == "ponytail"

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
