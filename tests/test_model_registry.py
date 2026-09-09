"""模型注册表（model_registry.py）单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from myagent.model_registry import ModelConfig, ModelRegistry
from myagent.providers import OpenAICompatibleProvider


def make_configs():
    return [
        ModelConfig(ref="default", model="m1", base_url="http://x", api_key="k"),
        ModelConfig(ref="gpt4o", model="gpt-4o", base_url="http://y", api_key="k2"),
    ]


def test_registry_creates_providers():
    reg = ModelRegistry(make_configs())
    assert reg.list_models() == ["default", "gpt4o"]
    assert reg.has("default")
    assert reg.has("gpt4o")
    assert not reg.has("nonexistent")


def test_get_provider_returns_openai_compatible():
    reg = ModelRegistry(make_configs())
    p = reg.get_provider("default")
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.model == "m1"


def test_get_provider_unknown_raises():
    reg = ModelRegistry(make_configs())
    with pytest.raises(KeyError):
        reg.get_provider("nonexistent")


def test_get_config():
    reg = ModelRegistry(make_configs())
    mc = reg.get_config("gpt4o")
    assert mc.model == "gpt-4o"
    assert mc.base_url == "http://y"


def test_unsupported_provider_raises():
    with pytest.raises(ValueError):
        ModelRegistry([ModelConfig(ref="x", model="m", base_url="b", api_key="k", provider="anthropic")])
