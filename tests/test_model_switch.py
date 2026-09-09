"""模型切换（Agent.switch_model）单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from myagent.config import Config
from myagent.agent import Agent
from myagent.model_registry import ModelConfig


def make_config():
    return Config(
        model="m1",
        base_url="http://x",
        api_key="k",
        models=[
            ModelConfig(ref="default", model="m1", base_url="http://x", api_key="k"),
            ModelConfig(ref="vision", model="gpt-4o", base_url="http://y", api_key="k2"),
        ],
    )


def test_list_models():
    a = Agent(make_config(), confirm_callback=lambda p: False)
    assert a.list_models() == ["default", "vision"]


def test_current_model_default():
    a = Agent(make_config(), confirm_callback=lambda p: False)
    assert a.current_model() == "default"


def test_switch_model():
    a = Agent(make_config(), confirm_callback=lambda p: False)
    a.switch_model("vision")
    assert a.current_model() == "vision"
    assert a.runner.provider.model == "gpt-4o"


def test_switch_back_to_default():
    a = Agent(make_config(), confirm_callback=lambda p: False)
    a.switch_model("vision")
    a.switch_model("default")
    assert a.current_model() == "default"
    assert a.runner.provider.model == "m1"


def test_switch_unknown_model_raises():
    a = Agent(make_config(), confirm_callback=lambda p: False)
    with pytest.raises(KeyError):
        a.switch_model("nonexistent")
