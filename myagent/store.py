"""会话持久化（参考 Suna internal/memory/store.go）。

职责：用 SQLite 保存会话和消息，让 agent 重启后能接着聊。
对应 Suna 的 Store，用 Python 标准库 sqlite3 实现，不引入新依赖。

表结构：
  - sessions：会话元信息（id、标题、时间）
  - messages：消息（role、content、tool_call_id）
"""
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import List, Dict, Optional


class Store:
    """SQLite 会话存储。

    线程安全：agent 在 Textual 的 worker 线程里运行，会跨线程访问 Store。
    因此用 check_same_thread=False 允许跨线程，并用锁串行化所有写操作。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：允许 worker 线程访问（agent 在后台线程跑）
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 锁：串行化跨线程的读写，避免并发写冲突
        self._lock = threading.Lock()
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_call_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
        """)
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------- 会话 ----------
    def create_session(self, title: str = "") -> str:
        sid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (sid, title, now, now),
            )
            self.conn.commit()
        return sid

    def list_sessions(self) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_session_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, time.time(), session_id),
            )
            self.conn.commit()

    # ---------- 消息 ----------
    def save_message(self, session_id: str, role: str, content: str, tool_call_id: str = "") -> None:
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_call_id, now),
            )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            self.conn.commit()

    def load_messages(self, session_id: str) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT role, content, tool_call_id FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        messages = []
        for r in rows:
            msg = {"role": r["role"], "content": r["content"]}
            if r["tool_call_id"]:
                msg["tool_call_id"] = r["tool_call_id"]
            messages.append(msg)
        return messages
