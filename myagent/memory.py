"""记忆模块。

对应 Suna 的 internal/memory。负责三件事：
  1. 保存这次会话的所有消息（用户、助手、工具结果）。
  2. 在消息过长时做"上下文裁剪"，避免把模型塞爆或无限烧钱。
  3. 可选：把消息持久化到 SQLite，重启后能接着聊。

关键认知（很多人做 agent 最容易踩的坑）：
  - 大模型本身没有"记忆"，你每次调用都要把完整历史再发一遍。
  - 所以"记忆"就是管理一份消息列表 + 决定每次发多少。
  - 裁剪策略是本项目最需要留意的部分，也是最值得你继续演进的地方。

裁剪策略（参考 Suna compress.go）：
  - 按 token 数裁剪，而不是简单"留最近 N 条"。
  - 系统提示词永远保留在最前面。
  - 超过预算时，保留最近消息直到接近预算，丢弃最旧的。
  - （进阶：对丢弃的旧消息做 LLM 摘要压缩，对应 Suna 的 Session State。）
"""
from typing import List, Dict, Optional

from .store import title_from_user_input


# OpenAI 消息结构是一个字典列表，每个字典有 role/content 字段。
Message = Dict[str, str]
Messages = List[Message]


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    不引入 tiktoken，用启发式：中英文混合约 1 token ≈ 2 字符。
    够裁剪用，不需要精确。
    """
    if not text:
        return 0
    return max(1, len(text) // 2)


class Memory:
    """保存整段会话，按 token 上限裁剪，可选持久化。"""

    def __init__(
        self,
        max_history: int = 20,
        max_tokens: int = 8000,
        store: Optional[object] = None,
        session_id: Optional[str] = None,
    ):
        # 保存原始消息列表（含系统提示词、用户、助手、工具结果）。
        self._messages: Messages = []
        # 按条数裁剪上限（兜底）。
        self.max_history = max_history
        # 按 token 数裁剪上限（主策略）。
        self.max_tokens = max_tokens
        # 可选持久化。
        self.store = store
        self.session_id = session_id

    # ---------- 增消息 ----------
    def add_system(self, content: str) -> None:
        # 系统提示词不持久化：它由 Agent 每次启动重新注入，存了会重复。
        self._messages.append({"role": "system", "content": content})

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        self._persist("user", content)

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})
        self._persist("assistant", content)

    def add_tool(self, tool_call_id: str, content: str) -> None:
        # 工具结果在 OpenAI 协议里是独立角色 tool，
        # 必须带上对应的 tool_call_id 才能和上一次的调用对上（见 runner.py）。
        self._messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        )
        self._persist("tool", content, tool_call_id)

    def _persist(self, role: str, content: str, tool_call_id: str = "") -> None:
        if self.store is not None:
            if self.session_id is None:
                self.session_id = self.store.create_session()
            self.store.save_message(self.session_id, role, content, tool_call_id)
            if role == "user":
                title = title_from_user_input(content)
                if title:
                    self.store.update_session_title(self.session_id, title)

    # ---------- 读消息 ----------
    def snapshot(self) -> Messages:
        """返回给模型用的消息副本（已裁剪）。返回拷贝，避免外面改动内部状态。"""
        # 系统提示词固定在顶部，不参与裁剪。
        system = [m for m in self._messages if m["role"] == "system"]
        # 其余消息按 token 预算裁剪。
        rest = [m for m in self._messages if m["role"] != "system"]
        rest = self._trim_by_tokens(rest)
        # 兜底：再按条数裁剪。
        if len(rest) > self.max_history:
            rest = rest[-self.max_history:]
        return system + rest

    def _trim_by_tokens(self, messages: Messages) -> Messages:
        """按 token 预算裁剪：保留最近消息直到接近预算，丢弃最旧的。"""
        if self._estimate_tokens(messages) <= self.max_tokens:
            return messages
        kept: Messages = []
        total = 0
        for m in reversed(messages):
            t = estimate_tokens(m.get("content", ""))
            # 至少保留最新一条；之后严格服从预算。
            if kept and total + t > self.max_tokens:
                break
            kept.append(m)
            total += t
        kept.reverse()
        return kept

    @staticmethod
    def _estimate_tokens(messages: Messages) -> int:
        return sum(estimate_tokens(m.get("content", "")) for m in messages)

    # ---------- 持久化加载 ----------
    def load_from_store(self, session_id: str) -> None:
        """从 SQLite 加载历史消息（不含系统提示词，系统提示词由 Agent 重新注入）。"""
        if self.store is None:
            return
        self._messages = self.store.load_messages(session_id)
