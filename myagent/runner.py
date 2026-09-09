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
import time

from .guard import Guard, APPROVE, REJECT, CONFIRM
from .errors import classify_error, retryable, ModelError
from .providers import BaseProvider, OpenAICompatibleProvider

MAX_ITERATIONS = 8  # 单次对话最多允许的模型-工具往返轮数，防死循环

# 模型请求重试（参考 Suna runner.go 的 completeWithRecovery）
MODEL_MAX_RETRIES = 3        # 最多重试 3 次
MODEL_RETRY_DELAY = 8        # 每次重试间隔 8 秒（秒）
MODEL_TIMEOUT = 60           # 单次请求超时（秒）


class Runner:
    """持有模型 Provider，负责"一轮对话里反复调用模型直到出答案"。"""

    def __init__(
        self,
        config,
        guard: Optional[Guard] = None,
        confirm_callback=None,
        provider: Optional[BaseProvider] = None,
    ):
        # 模型 Provider（对应 Suna 的 Adapter）。
        # 不传则用主模型建一个 OpenAI 兼容 Provider。
        self.provider = provider or OpenAICompatibleProvider(
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=MODEL_TIMEOUT,
        )
        self.config = config
        self.guard = guard
        # confirm_callback(command) -> bool：ask 模式下询问用户是否放行。
        # 未提供时默认拒绝（fail-closed）。
        self.confirm_callback = confirm_callback
        # tool_callback(name, args) -> None：工具调用时回调（TUI 用它展示工具调用）。
        self.tool_callback = None

    def _one_call(
        self,
        messages: List[Dict],
        schemas: List[Dict],
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        """发一次请求，带重试（参考 Suna 的 completeWithRecovery）。

        只对【可重试错误】重试（网络抖动、限流、5xx），最多 MODEL_MAX_RETRIES 次。
        参数错误、鉴权失败等不可重试错误直接抛出。
        """
        for attempt in range(1, MODEL_MAX_RETRIES + 2):  # 1 + 重试次数
            try:
                return self._do_call(messages, schemas, on_delta)
            except Exception as exc:
                err = classify_error(exc)
                # 达到最大次数，或错误不可重试 → 抛出
                if attempt >= MODEL_MAX_RETRIES + 1 or not retryable(err):
                    raise
                # 可重试：等待后重试
                print(f"\n  [重试] 第{attempt}次失败 ({err.kind}), {MODEL_RETRY_DELAY}s 后重试...")
                time.sleep(MODEL_RETRY_DELAY)
        raise ModelError("unknown", "model request failed without an error")

    def _do_call(
        self,
        messages: List[Dict],
        schemas: List[Dict],
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Dict:
        """发一次请求（不重试），返回统一结构：{"content": str, "tool_calls": [..]}。

        实际调用委托给 Provider（对应 Suna 的 Adapter），
        Provider 内部处理流式/非流式，返回统一结构。
        """
        stream = on_delta is not None
        return self.provider.complete(
            messages=messages,
            tools=schemas,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            stream=stream,
            on_delta=on_delta,
        )

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

            if tool_calls:
                # 1) 把模型的这个调用意图放进消息，供继续对话
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })

                # 2) 逐个工具执行，把结果塞回消息
                for tc in tool_calls:
                    if self.tool_callback is not None:
                        self.tool_callback(
                            tc["function"]["name"], tc["function"]["arguments"]
                        )
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
