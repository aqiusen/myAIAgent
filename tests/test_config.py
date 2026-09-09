"""Config 配置读取测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.config import Config, PROJECT_ROOT


def test_relative_db_path_resolves_from_project_root(monkeypatch):
    monkeypatch.setenv("MY_AGENT_API_KEY", "k")
    monkeypatch.setenv("MY_AGENT_DB_PATH", "db/test.db")

    config = Config.from_env()

    assert config.db_path == str(PROJECT_ROOT / "db" / "test.db")
