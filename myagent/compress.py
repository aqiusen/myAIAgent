"""Session State 压缩（严格对照 Suna internal/memory/compress.go + runner/compression.go）。

WorkingMemory = 最近对话 keepRegion
Session State = 旧内容折叠后的固定结构，注入为独立 user 块，不进 system、不写回 working。
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional, Tuple


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 2)

MAX_TOOL_OUTPUT_LINES = 500
MAX_TOOL_OUTPUT_BYTES = 50 * 1024
MAX_COMPRESS_ASSISTANT_BYTES = 6 * 1024
MAX_COMPRESS_TOOL_RESULT_BYTES = 4 * 1024
MAX_COMPRESS_TOOL_ARGUMENT_BYTES = 2 * 1024
MAX_SESSION_STATE_TOKENS = 3000
MIN_SESSION_STATE_TOKENS = 1200
MIN_CONTEXT_MARGIN_TOKENS = 2048
RECENT_CHAT_USER_TURNS = 6
RECENT_TOOL_USER_TURNS = 2
MAX_RECENT_MESSAGES = 48

SESSION_STATE_PREFIX = "<session_state>"

COMPRESS_PROMPT = """Compress the following Suna conversation history into a bounded Session State for a general-purpose agent.

This output is internal working memory, not a user-facing summary. It must let a future agent continue the current session while also recalling earlier completed work or topics if the user asks. Prioritize accurate continuity over transcript detail.

Goals, in order:
1. Preserve the active context so the current task or conversation can continue without interruption.
2. Preserve completed work and earlier topics as a compact ledger, even when they are no longer active.
3. Preserve explicit user requirements, corrections, preferences, accepted decisions, and rejected directions.
4. Convert tool calls/results into concise facts: what was done, changed, discovered, failed, created, or verified.
5. Reduce token usage by dropping raw logs, redundant phrasing, stale speculation, and long file/output contents.

Rules:
- Write in the conversation's primary language.
- Be concise but specific. Prefer bullets.
- Do not invent facts.
- Preserve media reference summaries verbatim (lines like `[image: ...]` with a `source=` value); keep them in the ledger or active context so the agent can re-read the original media later. Do not summarize or drop them.
- Do not include raw tool logs or raw file contents unless an exact short snippet is essential.
- Merge with the previous Session State; do not append duplicate summaries.
- Keep the output bounded. Older completed work may become a one-line ledger item, but should not disappear if it may help recall the session.
- Keep section order exactly as specified. Use "- none" for empty sections.

Use this exact structure:

# Session State

## Active context
- ...

## Completed work / topic ledger
- ...

## User requirements and decisions
- ...

## Tool facts
- ...

## Open threads
- ...

## Recovery note
- ...

{previous}

New conversation history to fold into the Session State:

{content}
"""

CompleteFn = Callable[[str, int], str]
Message = Dict
Messages = List[Message]


def format_session_state_for_model(state: str) -> str:
    """对照 Suna FormatSessionStateForModel：独立内部上下文，不进 system。"""
    state = (state or "").strip()
    if not state:
        return ""
    return (
        SESSION_STATE_PREFIX + "\n"
        "This is internal session memory for continuity, not a user request. "
        "Current user instructions override it.\n\n"
        + state
        + "\n</session_state>"
    )


def inject_session_state(messages: Messages, state: str) -> Messages:
    """system 之后、历史之前插入 Session State user 块。"""
    formatted = format_session_state_for_model(state)
    if not formatted:
        return list(messages)
    systems = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return systems + [{"role": "user", "content": formatted}] + rest


def truncate_tool_output_for_context(content: str) -> str:
    """对照 Suna TruncateToolOutputForContext。"""
    if len(content) <= MAX_TOOL_OUTPUT_BYTES:
        return content
    lines = content.split("\n")
    if len(lines) <= MAX_TOOL_OUTPUT_LINES:
        return truncate_utf8(content, MAX_TOOL_OUTPUT_BYTES) + "\n... (truncated; full tool output omitted from model context)"
    kept = lines[:MAX_TOOL_OUTPUT_LINES]
    result = "\n".join(kept)
    if len(result) > MAX_TOOL_OUTPUT_BYTES:
        result = truncate_utf8(result, MAX_TOOL_OUTPUT_BYTES)
    return f"{result}\n... (truncated, {len(lines)} lines total; full tool output omitted from model context)"


def trim_tool_results_for_context(messages: Messages) -> Messages:
    """对照 Suna trimToolResultsForContext。"""
    out = []
    for msg in messages:
        if msg.get("role") != "tool":
            out.append(msg)
            continue
        text = msg.get("content") or ""
        trimmed = truncate_tool_output_for_context(text)
        if trimmed.strip() == text.strip():
            out.append(msg)
            continue
        copied = dict(msg)
        copied["content"] = trimmed
        out.append(copied)
    return out


def session_state_token_budget(context_window: int) -> int:
    if context_window <= 0:
        return 2000
    n = context_window // 100
    n = max(MIN_SESSION_STATE_TOKENS, min(MAX_SESSION_STATE_TOKENS, n))
    return n


def context_margin(context_window: int) -> int:
    margin = context_window // 200
    return max(MIN_CONTEXT_MARGIN_TOKENS, margin)


def usable_input_budget(context_window: int, output_budget: int) -> int:
    budget = context_window - output_budget - context_margin(context_window)
    return budget if budget >= 1 else 1


def estimator_safety_tokens(estimated: int, calibrated: bool = False) -> int:
    if estimated <= 0:
        return 0
    if calibrated:
        return max(2048, estimated // 40)
    return max(8192, estimated // 16)


def compact_context_tokens(estimated_input: int, calibrated: bool = False) -> int:
    if estimated_input <= 0:
        return 0
    return estimated_input + estimator_safety_tokens(estimated_input, calibrated)


def is_tool_heavy(messages: Messages) -> bool:
    if not messages:
        return False
    start = max(0, len(messages) - 24)
    tool_like = 0
    for msg in messages[start:]:
        if msg.get("role") == "tool" or _tool_calls(msg):
            tool_like += 1
    return tool_like >= 3


def choose_recent_keep_with_budget(messages: Messages, context_window: int, budget: int) -> int:
    """recent window 由代码按用户 turn 选择，不能交给 LLM。"""
    if len(messages) <= 1:
        return len(messages)
    target_turns = RECENT_TOOL_USER_TURNS if is_tool_heavy(messages) else RECENT_CHAT_USER_TURNS
    if budget <= 0:
        budget = _recent_window_token_budget(context_window, 0)
    turns = 0
    keep = 0
    tokens = 0
    for i in range(len(messages) - 1, -1, -1):
        if keep >= MAX_RECENT_MESSAGES:
            break
        msg_tokens = estimate_tokens(_message_blob(messages[i]))
        if keep > 0 and budget > 0 and tokens + msg_tokens > budget:
            break
        tokens += msg_tokens
        keep += 1
        if messages[i].get("role") == "user":
            turns += 1
            if turns >= target_turns:
                break
    if keep >= len(messages):
        keep = len(messages) - 1
    if keep < 1:
        keep = 1
    return keep


def recent_window_covers_all(messages: Messages, context_window: int, budget: int) -> bool:
    """recent 窗口已经覆盖全部对话时，折叠 1 条旧消息救不了预算（system/tools 才是大头）。"""
    if not messages:
        return True
    target_turns = RECENT_TOOL_USER_TURNS if is_tool_heavy(messages) else RECENT_CHAT_USER_TURNS
    if budget <= 0:
        budget = _recent_window_token_budget(context_window, 0)
    turns = 0
    keep = 0
    tokens = 0
    for i in range(len(messages) - 1, -1, -1):
        if keep >= MAX_RECENT_MESSAGES:
            return False
        msg_tokens = estimate_tokens(_message_blob(messages[i]))
        if keep > 0 and budget > 0 and tokens + msg_tokens > budget:
            return False
        tokens += msg_tokens
        keep += 1
        if messages[i].get("role") == "user":
            turns += 1
            if turns >= target_turns:
                return keep >= len(messages)
    return keep >= len(messages)


def expand_recent_start_for_tool_calls(messages: Messages, keep_start: int) -> int:
    """recent 里的 tool 结果必须带着产生它的 assistant tool_call。"""
    if keep_start <= 0 or keep_start >= len(messages):
        return keep_start
    while True:
        retained = set()
        for msg in messages[keep_start:]:
            if msg.get("role") != "assistant":
                continue
            for tc in _tool_calls(msg):
                if (tc.get("id") or "").strip():
                    retained.add(tc["id"])
        new_start = keep_start
        for msg in messages[keep_start:]:
            if msg.get("role") != "tool":
                continue
            call_id = (msg.get("tool_call_id") or "").strip()
            if not call_id or call_id in retained:
                continue
            parent = _find_tool_call_parent(messages, keep_start, call_id)
            if 0 <= parent < new_start:
                new_start = parent
        if new_start == keep_start:
            return keep_start
        keep_start = new_start


def format_compress_input(messages: Messages) -> str:
    parts = []
    for i, msg in enumerate(messages, start=1):
        chunk = format_compress_message(i, msg)
        if chunk:
            parts.append(chunk)
    return "\n".join(parts)


def format_compress_message(index: int, msg: Message) -> str:
    role = msg.get("role") or ""
    text = (msg.get("content") or "").strip()
    parts = []
    if role == "user" and text:
        parts.append(f'<user_message index="{index}">\n{text}\n</user_message>\n')
    elif role == "assistant" and text:
        parts.append(
            f'<assistant_message index="{index}" note="assistant proposal or response; preserve only if accepted or still relevant">\n'
            f"{truncate_middle(text, MAX_COMPRESS_ASSISTANT_BYTES)}\n</assistant_message>\n"
        )
    elif role == "tool" and text:
        call_id = msg.get("tool_call_id") or ""
        parts.append(
            f'<tool_result index="{index}" call_id="{call_id}" note="convert into action facts; do not keep raw logs">\n'
            f"{truncate_middle(text, MAX_COMPRESS_TOOL_RESULT_BYTES)}\n</tool_result>\n"
        )
    elif text:
        parts.append(
            f'<{role}_message index="{index}">\n'
            f"{truncate_middle(text, MAX_COMPRESS_ASSISTANT_BYTES)}\n</{role}_message>\n"
        )
    for tc in _tool_calls(msg):
        parts.append(
            f'<tool_call index="{index}" name="{tc.get("name") or ""}">\n'
            f"{truncate_middle(tc.get('arguments') or '', MAX_COMPRESS_TOOL_ARGUMENT_BYTES)}\n</tool_call>\n"
        )
    return "".join(parts)


def render_compress_prompt(previous_state: str, content: str) -> str:
    previous = ""
    if (previous_state or "").strip():
        previous = (
            "Previous Session State to merge and update:\n\n"
            + previous_state.strip()
            + "\n"
        )
    return COMPRESS_PROMPT.format(previous=previous, content=content)


def compress_history_keeping_state(
    messages: Messages,
    previous_state: str,
    complete_fn: CompleteFn,
    keep_recent: int = 0,
    context_window: int = 0,
    output_budget: int = 0,
    recent_token_budget: int = 0,
) -> Tuple[Messages, str, int]:
    """对照 compressHistoryKeepingState。返回 (keepRegion, sessionState, foldedCount)。"""
    if not messages:
        return messages, "", 0
    keep = keep_recent
    if keep <= 0:
        keep = choose_recent_keep_with_budget(messages, context_window, recent_token_budget)
    if len(messages) <= keep:
        keep = len(messages) - 1
    if keep < 1:
        keep = 1
    keep_start = len(messages) - keep
    if keep_start <= 0:
        if not (previous_state or "").strip():
            return messages, "", 0
        keep_start = max(1, len(messages) - 1)
    keep_start = expand_recent_start_for_tool_calls(messages, keep_start)
    compress_region = messages[:keep_start]
    keep_region = messages[keep_start:]
    compress_input = format_compress_input(compress_region)
    if not compress_input.strip() and not (previous_state or "").strip():
        return messages, "", 0
    prompt = render_compress_prompt(previous_state, compress_input)
    if not prompt.strip():
        raise ValueError("render compress prompt: empty prompt")
    max_tokens = output_budget if output_budget > 0 else session_state_token_budget(context_window)
    state = (complete_fn(prompt, max_tokens) or "").strip()
    if not state:
        raise ValueError("compressor returned empty session state")
    return keep_region, state, len(compress_region)


def should_compact_messages(
    system: str,
    session_state: str,
    messages: Messages,
    tools: Optional[list],
    context_window: int,
    output_budget: int,
) -> bool:
    """对照 shouldCompactRequest（无校准，coef=1）。小窗口时安全垫按窗口缩放。"""
    if context_window <= 0:
        return False
    estimated = estimate_tokens(system)
    estimated += estimate_tokens(format_session_state_for_model(session_state))
    estimated += sum(estimate_tokens(_message_blob(m)) for m in messages)
    if tools:
        estimated += estimate_tokens(json.dumps(tools, ensure_ascii=False))
    safety = estimator_safety_tokens(estimated, calibrated=False)
    if context_window < 32000:
        safety = max(context_window // 16, 256)
    return estimated + safety > usable_input_budget(context_window, output_budget)


def truncate_utf8(s: str, max_bytes: int) -> str:
    if max_bytes <= 0 or len(s.encode("utf-8")) <= max_bytes:
        return s[:max_bytes] if max_bytes > 0 and len(s) > max_bytes else s
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    clipped = encoded[:max_bytes]
    return clipped.decode("utf-8", errors="ignore")


def truncate_middle(s: str, max_bytes: int) -> str:
    if max_bytes <= 0 or len(s) <= max_bytes:
        return s
    half = max_bytes // 2
    if half <= 0:
        return "... (truncated)"
    prefix = s[:half]
    suffix_budget = max_bytes - len(prefix)
    if suffix_budget <= 0:
        return prefix + "\n... (truncated)"
    suffix = s[-suffix_budget:]
    omitted = len(s) - len(prefix) - len(suffix)
    return prefix + f"\n... (truncated, {omitted} bytes omitted) ...\n" + suffix


def _recent_window_token_budget(context_window: int, output_budget: int) -> int:
    if context_window <= 0:
        return 0
    budget = (
        context_window
        - output_budget
        - session_state_token_budget(context_window)
        - context_margin(context_window)
    )
    return budget if budget >= 1 else 1


def _tool_calls(msg: Message) -> List[dict]:
    raw = msg.get("tool_calls") or []
    out = []
    for tc in raw:
        fn = tc.get("function") if isinstance(tc, dict) else {}
        fn = fn or {}
        out.append({
            "id": (tc.get("id") if isinstance(tc, dict) else "") or "",
            "name": fn.get("name") or (tc.get("name") if isinstance(tc, dict) else "") or "",
            "arguments": fn.get("arguments") or (tc.get("arguments") if isinstance(tc, dict) else "") or "",
        })
    return out


def _find_tool_call_parent(messages: Messages, before: int, call_id: str) -> int:
    for i in range(before - 1, -1, -1):
        if messages[i].get("role") != "assistant":
            continue
        for tc in _tool_calls(messages[i]):
            if tc.get("id") == call_id:
                return i
    return -1


def _message_blob(msg: Message) -> str:
    parts = [msg.get("content") or ""]
    for tc in _tool_calls(msg):
        parts.append(tc.get("name") or "")
        parts.append(tc.get("arguments") or "")
    return "\n".join(parts)
