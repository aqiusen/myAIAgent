"""Subtask / spawn。对照 Suna internal/subtask/subtask_test.go。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.subtask import (
    SIDE_NONE,
    SIDE_UNKNOWN,
    STATUS_COMPLETED,
    STATUS_COMPLETED_UNSTRUCTURED,
    STATUS_FAILED,
    failed_result,
    parse_final_result,
)
from myagent.tools.base import can_grant_to_subtask
from myagent.tools.spawn_provider import make_spawn_tool, spawn_tool_names
from myagent.tools.builtin import TOOLS
from myagent.agent import Agent
from myagent.config import Config
from myagent.model_registry import ModelConfig


def test_parse_structured_side_effects():
    got = parse_final_result(
        '{"result":"done","side_effects":{"status":"remaining","summary":"modified requested files","paths":["a.txt"]}}'
    )
    assert got.status == STATUS_COMPLETED
    assert got.text == "done"
    assert got.side_effects.status == "remaining"
    assert got.side_effects.paths == ["a.txt"]


def test_parse_unstructured_marks_unknown():
    got = parse_final_result("plain answer")
    assert got.status == STATUS_COMPLETED_UNSTRUCTURED
    assert got.text == "plain answer"
    assert got.side_effects.status == SIDE_UNKNOWN


def test_parse_unsupported_side_effects_status():
    got = parse_final_result('{"result":"done","side_effects":{"status":"maybe","summary":"custom"}}')
    assert got.status == STATUS_COMPLETED
    assert got.side_effects.status == SIDE_UNKNOWN
    assert "unsupported" in got.side_effects.summary
    assert "custom" in got.side_effects.summary


def test_failed_result_uses_tool_call_for_side_effects():
    without_tool = failed_result("boom", False)
    assert without_tool.status == STATUS_FAILED
    assert without_tool.side_effects.status == SIDE_NONE
    with_tool = failed_result("boom", True)
    assert with_tool.side_effects.status == SIDE_UNKNOWN


def test_parse_fenced_json():
    got = parse_final_result('```json\n{"result":"done","side_effects":{"status":"none"}}\n```')
    assert got.status == STATUS_COMPLETED
    assert got.text == "done"
    assert got.side_effects.status == SIDE_NONE


def test_spawn_schema_enum_only_grantable_tools():
    names = spawn_tool_names(TOOLS)
    assert "read_file" in names
    assert "spawn" not in names
    spec = make_spawn_tool(TOOLS, lambda **k: "x")
    enum = spec.parameters["properties"]["tools"]["items"].get("enum")
    assert "read_file" in enum
    assert spec.source == "agent"
    assert not can_grant_to_subtask(spec)


def test_builtin_tools_are_grantable():
    assert all(can_grant_to_subtask(t) for t in TOOLS)


def make_config(db_path, **kwargs):
    return Config(
        model="m",
        base_url="http://x",
        api_key="k",
        models=[ModelConfig(ref="default", model="m", base_url="http://x", api_key="k")],
        db_path=db_path,
        **kwargs,
    )


class FakeProvider:
    def complete(self, messages, tools, temperature, max_tokens, stream, on_delta=None, session_state=""):
        return {
            "content": '{"result":"looks good","side_effects":{"status":"none"}}',
            "tool_calls": [],
        }


def test_execute_spawn_isolated_and_structured(tmp_path):
    agent = Agent(make_config(str(tmp_path / "t.db")))
    agent.registry._providers["default"] = FakeProvider()
    out = agent.execute_spawn(task="review this file", model="default", tools=["read_file"])
    assert '"status": "completed"' in out or '"status":"completed"' in out.replace(" ", "")
    assert "looks good" in out


def test_execute_spawn_rejects_nested_and_invalid(tmp_path):
    agent = Agent(make_config(str(tmp_path / "t.db")))
    agent._in_subtask = True
    assert "not available to subtasks" in agent.execute_spawn(task="x", model="default", tools=[])
    agent._in_subtask = False
    assert "invalid spawn model" in agent.execute_spawn(task="x", model="nope", tools=[])
    assert "invalid spawn tool" in agent.execute_spawn(task="x", model="default", tools=["spawn"])
    assert "task is required" in agent.execute_spawn(task="", model="default", tools=[])


def test_agent_exposes_spawn_and_models_in_prompt(tmp_path):
    agent = Agent(make_config(str(tmp_path / "t.db")))
    names = {t.name for t in agent.tools_list}
    assert "spawn" in names
    prompt = agent._build_system_prompt()
    assert "Available subtask models" in prompt
    assert "default" in prompt
