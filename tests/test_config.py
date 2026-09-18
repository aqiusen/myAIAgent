"""Config 配置读取测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.config import Config, PROJECT_ROOT


def test_default_context_window_matches_suna(monkeypatch):
    monkeypatch.setenv("MY_AGENT_API_KEY", "k")
    monkeypatch.delenv("MY_AGENT_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("MY_AGENT_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("MY_AGENT_MAX_TOKENS", raising=False)

    config = Config.from_env()

    assert config.context_window == 128000
    assert config.max_output_tokens == 8192
    assert config.models[0].context_window == 128000
    assert config.models[0].max_output_tokens == 8192


def test_old_max_tokens_env_does_not_shrink_window(monkeypatch):
    monkeypatch.setenv("MY_AGENT_API_KEY", "k")
    monkeypatch.setenv("MY_AGENT_MAX_TOKENS", "8000")
    monkeypatch.delenv("MY_AGENT_CONTEXT_WINDOW", raising=False)

    config = Config.from_env()

    assert config.context_window == 128000


def test_output_tokens_must_be_smaller_than_window(monkeypatch):
    monkeypatch.setenv("MY_AGENT_API_KEY", "k")
    monkeypatch.setenv("MY_AGENT_CONTEXT_WINDOW", "1000")
    monkeypatch.setenv("MY_AGENT_MAX_OUTPUT_TOKENS", "1000")
    try:
        Config.from_env()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "必须小于" in str(exc)


def test_relative_db_path_resolves_from_project_root(monkeypatch):
    monkeypatch.setenv("MY_AGENT_API_KEY", "k")
    monkeypatch.setenv("MY_AGENT_DB_PATH", "db/test.db")

    config = Config.from_env()

    assert config.db_path == str(PROJECT_ROOT / "db" / "test.db")
