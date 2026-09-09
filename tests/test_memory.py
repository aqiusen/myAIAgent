"""Memory（token 裁剪 + 持久化集成）单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.memory import Memory, estimate_tokens
from myagent.store import Store


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 2  # 4 字符 / 2
    assert estimate_tokens("hello world") == 5  # 11 字符 / 2


def test_snapshot_keeps_system_prompt():
    m = Memory(max_tokens=1000)
    m.add_system("系统提示词")
    m.add_user("你好")
    m.add_assistant("你好！")
    snap = m.snapshot()
    assert snap[0]["role"] == "system"
    assert snap[0]["content"] == "系统提示词"


def test_token_trimming_drops_oldest():
    m = Memory(max_tokens=100)
    m.add_system("系统提示词")
    for i in range(20):
        m.add_user(f"这是第{i}条用户消息，内容比较长一些")
        m.add_assistant(f"这是第{i}条助手回复")
    snap = m.snapshot()
    # 裁剪后 token 不超过预算
    total = sum(estimate_tokens(x.get("content", "")) for x in snap)
    assert total <= 100
    # 系统提示词仍在最前
    assert snap[0]["role"] == "system"
    # 保留的是最近的消息
    assert "第19条" in snap[-1]["content"]


def test_no_trim_when_under_budget():
    m = Memory(max_tokens=10000)
    m.add_system("系统提示词")
    m.add_user("你好")
    m.add_assistant("你好！")
    snap = m.snapshot()
    assert len(snap) == 3  # system + user + assistant


def test_max_history_fallback():
    m = Memory(max_tokens=100000, max_history=5)
    m.add_system("系统提示词")
    for i in range(10):
        m.add_user(f"消息{i}")
        m.add_assistant(f"回复{i}")
    snap = m.snapshot()
    # 兜底按条数裁剪：system + 最近 5 条
    assert len(snap) == 6


def test_persistence_integration(tmp_path):
    """Memory 与 Store 集成：消息自动持久化，可重新加载。"""
    db = str(tmp_path / "test.db")
    store = Store(db)
    sid = store.create_session()

    m = Memory(store=store, session_id=sid)
    m.add_user("你好")
    m.add_assistant("你好！")
    m.add_tool("call_1", "date 输出")

    # 从 store 重新加载（不含系统提示词）
    loaded = store.load_messages(sid)
    assert len(loaded) == 3
    assert loaded[0]["role"] == "user"
    assert loaded[2]["tool_call_id"] == "call_1"
    store.close()


def test_user_message_updates_session_title(tmp_path):
    """用户消息持久化时，用最新一条用户输入更新会话标题。"""
    db = str(tmp_path / "test.db")
    store = Store(db)
    sid = store.create_session()

    m = Memory(store=store, session_id=sid)
    m.add_user("第一句话")
    assert store.get_session(sid)["title"] == "第一句话"

    m.add_assistant("回复")
    m.add_user("第二句话")
    assert store.get_session(sid)["title"] == "第二句话"
    store.close()


def test_system_prompt_not_persisted(tmp_path):
    """系统提示词不应持久化（重启会重复）。"""
    db = str(tmp_path / "test.db")
    store = Store(db)
    sid = store.create_session()

    m = Memory(store=store, session_id=sid)
    m.add_system("系统提示词")
    m.add_user("你好")

    loaded = store.load_messages(sid)
    roles = [x["role"] for x in loaded]
    assert "system" not in roles  # 系统提示词不落库
    store.close()
