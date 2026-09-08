"""记忆模块。

对应 Suna 的 internal/memory。负责两件事：
  1. 保存这次会话的所有消息（用户、助手、工具结果）。
  2. 在消息过长时做"上下文裁剪"，避免把模型塞爆或无限烧钱。

关键认知（很多人做 agent 最容易踩的坑）：
  - 大模型本身没有"记忆"，你每次调用都要把完整历史再发一遍。
  - 所以"记忆"就是管理一份消息列表 + 决定每次发多少。
  - 裁剪策略是本项目最需要留意的部分，也是最值得你继续演进的地方
    （比如摘要式裁剪、按 token 数裁剪，见文档）。
"""
from typing import List, Dict


# OpenAI 消息结构是一个字典列表，每个字典有 role/content 字段。
# 这里用类型别名表达"消息"，比到处写 Dict[str, str] 更清晰。
Message = Dict[str, str]
Messages = List[Message]


class Memory:
    """保存整段会话，并按上限截断后提供给 runner。"""

    def __init__(self, max_history: int = 20):
        # 保存原始消息列表（含系统提示词、用户、助手、工具结果）。
        self._messages: Messages = []
        # 裁剪上限。注意：系统提示词不参与裁剪，它永远在最前面。
        self.max_history = max_history

    # ---------- 增消息 ----------
    def add_system(self, content: str) -> None:
        self._messages.append({"role": "system", "content": content})

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_tool(self, tool_call_id: str, content: str) -> None:
        # 工具结果在 OpenAI 协议里是独立角色 tool，
        # 必须带上对应的 tool_call_id 才能和上一次的调用对上（见 runner.py）。
        self._messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        )

    # ---------- 读消息 ----------
    def snapshot(self) -> Messages:
        """返回给模型用的消息副本（已裁剪）。返回拷贝，避免外面改动内部状态。"""
        # 系统提示词固定在顶部，不参与裁剪。
        system = [m for m in self._messages if m["role"] == "system"]
        # 其余消息只保留最近 max_history 条。
        rest = [m for m in self._messages if m["role"] != "system"]
        if len(rest) > self.max_history:
            rest = rest[-self.max_history:]
        return system + rest
