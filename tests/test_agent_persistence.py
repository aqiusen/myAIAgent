"""Agent 持久化集成测试（不碰真实 API）。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.config import Config
from myagent.agent import Agent
from myagent.store import Store


def make_config(db_path):
    return Config(
        model="m",
        base_url="http://x",
        api_key="k",
        db_path=db_path,
    )


def test_agent_creates_session(tmp_path):
    c = make_config(str(tmp_path / "test.db"))
    a = Agent(c, confirm_callback=lambda p: False)
    assert a.session_id is not None
    assert a.store is not None
    # 会话已落库
    assert a.store.get_session(a.session_id) is not None


def test_agent_persists_messages(tmp_path):
    c = make_config(str(tmp_path / "test.db"))
    a = Agent(c, confirm_callback=lambda p: False)
    a.memory.add_user("你好")
    a.memory.add_assistant("你好！")
    a.memory.add_tool("call_1", "date 输出")

    # 从 store 验证
    msgs = a.store.load_messages(a.session_id)
    assert len(msgs) == 3
    assert msgs[0]["role"] == "user"


def test_agent_reloads_session(tmp_path):
    """模拟重启：用同一 session_id 重新建 Agent，能加载历史。"""
    c = make_config(str(tmp_path / "test.db"))
    a = Agent(c, confirm_callback=lambda p: False)
    a.memory.add_user("你好")
    a.memory.add_assistant("你好！")

    # 重启
    a2 = Agent(c, confirm_callback=lambda p: False, session_id=a.session_id)
    roles = [m["role"] for m in a2.memory._messages]
    assert "user" in roles
    assert "assistant" in roles
    # 系统提示词只出现一次（不重复）
    assert roles.count("system") == 1


def test_agent_without_db_no_store(tmp_path):
    c = Config(model="m", base_url="http://x", api_key="k", db_path="")
    a = Agent(c, confirm_callback=lambda p: False)
    assert a.store is None
    assert a.session_id is None
