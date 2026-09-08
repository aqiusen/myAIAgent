"""模型调用循环（Runner）。

对应 Suna 的 internal/runner，这是整个 agent 的"心脏"，一定要读懂。

它只负责一件事：**给定一组消息和工具，反复调用模型，直到模型给出最终答案**。
核心是一个 loop：

    ┌─────────────┐
    │ 发消息给模型  │
    └─────┬───────┘
          │
    ┌─────▼───────┐  想要调用工具？
    │ 模型返回内容  │ ────────────────┐
    │ + 可能带工具 │                 │ 是
    │    调用      │                 │
    └─────┬───────┘                 │
          │ 否（纯文字 / 没有工具调用） │
    ┌─────▼───────┐           ┌─────▼───────────┐
    │ 结束，返回答案 │           │ 执行工具拿到结果  │
    └─────────────┘           │ 把结果回填给模型   │
                              └─────┬───────────┘
                                    │ 继续循环
                                    ▼

为什么必须这样循环：
  - 大模型一次只能"想一步"。它先说要调 tools，我们再执行，把结果塞回消息，
    它才能基于结果往下走。有时它要连续调好几个工具才愿意收尾。
  - 所以一定要有"最大轮数"保护（MAX_ITERATIONS），防止模型在工具里死循环烧钱。

本版本支持流式输出（边生成边打字），见 _one_call 里的 stream 分支。
"""
from typing import List, Dict, Optional, Callable
from openai import OpenAI

from .guard import Guard, APPROVE, REJECT, CONFIRM

MAX_ITERATIONS = 8  # 单次对话最多允许的模型-工具往返轮数，防死循环


class Runner:
    """持有模型客户端，负责"一轮对话里反复调用模型直到出答案"。"""

    def __init__(self, config, guard: Optional[Guard] = None, confirm_callback=None):
        # OpenAI SDK 的客户端。传 base_url 就能对接任意 OpenAI 兼容端点。
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self.config = config
        self.guard = guard
        # confirm_callback(command) -> bool：ask 模式下询问用户是否放行。
        # 未提供时默认拒绝（fail-closed）。
        self.confirm_callback = confirm_callback

    def _one_call(
        self,
        messages: List[Dict],
        schemas: List[Dict],
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        """发一次请求，返回统一结构：{"content": str, "tool_calls": [..]}。

        阶段 1.2 新增：支持流式。
          - 不传 on_delta → 非流式，一次性拿完整结果（老行为）。
          - 传了 on_delta → stream=True，每来一段文字就回调 on_delta(text)，
            让调用方（CLI）边生成边打印。

        为什么返回统一结构而不是原始 response？
          因为流式和非流式拿到的对象长得不一样（一个是 chunk 流、一个是完整对象），
          把差异收敛在这里，run() 主循环就不用关心是哪种模式了。
        """
        stream = on_delta is not None
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            tools=schemas,                      # 把工具声明交给模型
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
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

        # 把按 index 归并好的 dict 转成和上面非流式一样的结构
        tool_calls_list = [
            {
                "id": entry["id"],
                "type": "function",
                "function": {"name": entry["name"], "arguments": entry["arguments"]},
            }
            for _, entry in sorted(tool_calls.items())
        ]
        return {"content": "".join(content_parts), "tool_calls": tool_calls_list}

    def run(
        self,
        messages: List[Dict],
        schemas: List[Dict],
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        """主循环：反复调用模型，处理工具调用，最终返回纯文本答案。"""
        for _ in range(MAX_ITERATIONS):
            result = self._one_call(messages, schemas, on_delta)
            content = result["content"]
            tool_calls = result["tool_calls"]

            # 调试：打印模型这一轮返回的原始结构（含 tool_calls）
            print("\n[DEBUG] 模型本轮返回:", result)

            if tool_calls:
                # 1) 把模型的这个调用意图放进消息，供继续对话
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })

                # 2) 逐个工具执行，把结果塞回消息
                for tc in tool_calls:
                    print(f"\n  [工具调用] {tc['function']['name']}({tc['function']['arguments']})")
                    result_text = self._dispatch(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })

                # 3) 带上工具结果，继续循环，让模型基于结果给下一步
                continue

            # 没有工具调用 → 模型给的就是最终答案
            return content

    # 工具执行路由：解析模型给的工具调用，先过 Guard，再调到 builtin 里的执行函数
    def _dispatch(self, tc: Dict) -> str:
        from . import tools
        name = tc["function"]["name"]
        tool = tools.tool_for(name)
        if tool is None:
            return f"未找到工具: {name}"
        # arguments 是模型生成的 JSON 字符串，解析成关键字参数
        import json
        try:
            kwargs = json.loads(tc["function"]["arguments"] or "{}")
        except Exception as exc:
            return f"工具参数解析失败: {exc}"

        # ---- Guard 安全检查（参考 Suna internal/guard）----
        if self.guard is not None:
            result = self.guard.check(name, kwargs)
            if result.decision == REJECT:
                # 拒绝：把原因回给模型，让它换一种安全做法
                return f"[Guard 拒绝] {result.reason}"
            if result.decision == CONFIRM:
                # 询问用户：放行则执行，否则拒绝
                if self.confirm_callback is not None and self.confirm_callback(kwargs):
                    return tool.run(**kwargs)
                return f"[Guard 拒绝] 用户未确认该操作"

        return tool.run(**kwargs)
