"""Store（SQLite 持久化）单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.store import Store, title_from_user_input


def test_title_from_user_input():
    assert title_from_user_input("  你好\n世界  ") == "你好 世界"
    assert title_from_user_input("a" * 31) == ("a" * 30) + "..."


def test_create_and_list_session(tmp_path):
    db = str(tmp_path / "test.db")
    s = Store(db)
    sid = s.create_session("测试会话")
    sessions = s.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["title"] == "测试会话"
    s.close()


def test_save_and_load_messages(tmp_path):
    db = str(tmp_path / "test.db")
    s = Store(db)
    sid = s.create_session()
    s.save_message(sid, "user", "你好")
    s.save_message(sid, "assistant", "你好！")
    s.save_message(sid, "tool", "date 输出", "call_1")

    msgs = s.load_messages(sid)
    assert len(msgs) == 3
    assert msgs[0] == {"role": "user", "content": "你好"}
    assert msgs[1] == {"role": "assistant", "content": "你好！"}
    # 工具消息带 tool_call_id
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "call_1"
    s.close()


def test_multiple_sessions_isolated(tmp_path):
    db = str(tmp_path / "test.db")
    s = Store(db)
    sid1 = s.create_session("会话1")
    sid2 = s.create_session("会话2")
    s.save_message(sid1, "user", "只属于会话1")
    s.save_message(sid2, "user", "只属于会话2")

    assert len(s.load_messages(sid1)) == 1
    assert len(s.load_messages(sid2)) == 1
    assert s.load_messages(sid1)[0]["content"] == "只属于会话1"
    assert s.load_messages(sid2)[0]["content"] == "只属于会话2"
    s.close()


def test_get_session(tmp_path):
    db = str(tmp_path / "test.db")
    s = Store(db)
    sid = s.create_session("标题")
    got = s.get_session(sid)
    assert got is not None
    assert got["title"] == "标题"
    assert s.get_session("不存在的id") is None
    s.close()


def test_update_session_title(tmp_path):
    db = str(tmp_path / "test.db")
    s = Store(db)
    sid = s.create_session("旧标题")
    s.update_session_title(sid, "新标题")
    assert s.get_session(sid)["title"] == "新标题"
    s.close()


def test_thread_safe_cross_thread_access(tmp_path):
    """跨线程访问 Store 不应报 SQLite 线程错误。"""
    import threading

    db = str(tmp_path / "test.db")
    s = Store(db)  # 主线程创建
    sid = s.create_session()

    errors = []

    def worker():
        try:
            for i in range(20):
                s.save_message(sid, "user", f"消息{i}")
            s.load_messages(sid)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"跨线程访问报错: {errors}"
    # 3 个线程各存 20 条 = 60 条
    assert len(s.load_messages(sid)) == 60
    s.close()
