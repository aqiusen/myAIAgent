"""模型提供商抽象（参考 Suna internal/model/adapter.go）。

职责：把"不同提供商怎么调模型"的差异收敛到一个统一接口 complete()，
让上层（runner）不用关心底层是 OpenAI、DeepSeek 还是 Ollama。

对应 Suna 的 Adapter：
  - BaseProvider          抽象接口（对应 Adapter 接口）
  - OpenAICompatibleProvider  OpenAI 兼容端点实现（对应 OpenAIChatAdapter）

为什么需要抽象？
  现在项目只支持 OpenAI 兼容端点（DeepSeek/Ollama 都能用，因为都兼容）。
  但以后要接 Anthropic 等不兼容的提供商时，只需新增一个 Provider 类，
  上层代码完全不用改。这就是"面向接口编程"的价值。
"""
from typing import Callable, Dict, List, Optional

from openai import OpenAI


class BaseProvider:
    """模型提供商抽象接口（对应 Suna 的 Adapter）。

    所有提供商实现这个接口，统一返回 {"content": str, "tool_calls": [..]}。
    """

    def complete(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float,
        max_tokens: int,
        stream: bool,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        """发一次请求，返回统一结构。

        Args:
            messages: 消息列表
            tools: 工具声明
            temperature: 温度
            max_tokens: 最大生成 token
            stream: 是否流式
            on_delta: 流式时每段文字回调

        Returns:
            {"content": str, "tool_calls": [{"id","type","function":{"name","arguments"}}]}
        """
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容端点提供商（OpenAI/DeepSeek/Ollama 等）。

    对应 Suna 的 OpenAIChatAdapter。用 OpenAI SDK，传 base_url 就能对接
    任何 OpenAI 兼容端点。
    """

    def __init__(self, model: str, base_url: str, api_key: str, timeout: int = 60):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def complete(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float,
        max_tokens: int,
        stream: bool,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )

        if not stream:
            # ---- 非流式：一次性拿完整结果 ----
            msg = response.choices[0].message
            return {
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (getattr(msg, "tool_calls", None) or [])
                ],
            }

        # ---- 流式：逐 chunk 累加 ----
        # 流式下模型把内容拆成很多小片（chunk）发过来，我们要自己拼回去。
        # 难点：tool_calls 也是分片来的，每个分片带一个 index，
        #       必须按 index 归并，arguments 是逐段拼接的字符串。
        content_parts: List[str] = []
        tool_calls: Dict[int, Dict] = {}  # index -> {id, name, arguments}

        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # 文字部分：累加 + 实时回调给调用方打印
            if delta.content:
                content_parts.append(delta.content)
                on_delta(delta.content)

            # 工具调用部分：按 index 归并
            for tc in (getattr(delta, "tool_calls", None) or []):
                entry = tool_calls.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    entry["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        entry["name"] = tc.function.name
                    if tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

        # 把按 index 归并好的 dict 转成统一结构
        tool_calls_list = [
            {
                "id": entry["id"],
                "type": "function",
                "function": {"name": entry["name"], "arguments": entry["arguments"]},
            }
            for _, entry in sorted(tool_calls.items())
        ]
        return {"content": "".join(content_parts), "tool_calls": tool_calls_list}
