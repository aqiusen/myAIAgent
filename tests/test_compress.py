"""Session State 压缩。对照 Suna internal/memory/compress_test.go。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.compress import (
    choose_recent_keep_with_budget,
    compress_history_keeping_state,
    expand_recent_start_for_tool_calls,
    format_compress_input,
    format_session_state_for_model,
    inject_session_state,
    render_compress_prompt,
    truncate_tool_output_for_context,
)
from myagent.memory import Memory


SUMMARY = "# Session State\n\n## Active context\n- continue task"


def test_compress_history_builds_session_state():
    captured = {}

    def complete_fn(prompt, max_tokens):
        captured["prompt"] = prompt
        return SUMMARY

    messages = [
        {"role": "user", "content": "我要一个最小改动方案，同时记住之前讨论的约束。" + "补充背景。" * 80},
        {"role": "assistant", "content": "可以新增很多协议字段。"},
        {"role": "user", "content": "不要新增复杂语义，复用现有 compact。"},
        {"role": "assistant", "content": "好的，改为 continuation state。"},
    ]
    compressed, summary, folded = compress_history_keeping_state(
        messages, "", complete_fn, keep_recent=1, context_window=0, output_budget=0,
    )
    assert summary == SUMMARY
    assert folded == 3
    assert len(compressed) == 1
    assert compressed[0]["content"] == "好的，改为 continuation state。"
    prompt = captured["prompt"]
    for want in (
        "# Session State",
        "## Completed work / topic ledger",
        "## User requirements and decisions",
        "<user_message",
        "不要新增复杂语义",
    ):
        assert want in prompt


def test_compress_preserves_tool_call_parent():
    messages = [
        {"role": "user", "content": "older request"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-1", "function": {"name": "readfile", "arguments": '{"path":"a"}'}}
        ]},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]
    compressed, _, _ = compress_history_keeping_state(
        messages, "", lambda p, m: SUMMARY, keep_recent=1, context_window=400000, output_budget=0,
    )
    assert len(compressed) == 2
    assert compressed[0]["role"] == "assistant"
    assert compressed[0]["tool_calls"][0]["id"] == "call-1"
    assert compressed[1]["role"] == "tool"
    assert compressed[1]["tool_call_id"] == "call-1"


def test_compress_preserves_parallel_tool_parents():
    messages = [
        {"role": "user", "content": "older request"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-a", "function": {"name": "readfile", "arguments": '{"path":"a"}'}},
            {"id": "call-b", "function": {"name": "readfile", "arguments": '{"path":"b"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call-a", "content": "result a"},
        {"role": "tool", "tool_call_id": "call-b", "content": "result b"},
    ]
    compressed, _, _ = compress_history_keeping_state(
        messages, "", lambda p, m: SUMMARY, keep_recent=1, context_window=400000, output_budget=0,
    )
    assert len(compressed) == 3
    ids = {tc["id"] for tc in compressed[0]["tool_calls"]}
    assert ids == {"call-a", "call-b"}


def test_format_session_state_wrapper():
    wrapped = format_session_state_for_model("早期决策：保持独立字段。")
    assert wrapped.startswith("<session_state>")
    assert "not a user request" in wrapped
    assert "早期决策：保持独立字段。" in wrapped
    assert wrapped.endswith("</session_state>")
    assert format_session_state_for_model("  ") == ""


def test_inject_session_state_after_system():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    out = inject_session_state(msgs, "# Session State\n- x")
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert "<session_state>" in out[1]["content"]
    assert out[2]["content"] == "hi"


def test_empty_compressor_result_errors():
    try:
        compress_history_keeping_state(
            [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
            "", lambda p, m: "  ", keep_recent=1,
        )
        assert False, "expected empty state error"
    except ValueError as exc:
        assert "empty session state" in str(exc)


def test_choose_recent_keep_user_turns():
    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"u{i}"})
        messages.append({"role": "assistant", "content": f"a{i}"})
    keep = choose_recent_keep_with_budget(messages, 100000, 100000)
    # 6 user turns from the end, plus their assistants
    assert keep >= 6
    assert keep < len(messages)


def test_memory_snapshot_without_complete_fn_still_trims():
    m = Memory(max_tokens=80)
    m.add_system("系统提示词")
    for i in range(20):
        m.add_user(f"这是第{i}条用户消息，内容比较长一些")
        m.add_assistant(f"这是第{i}条助手回复")
    snap = m.snapshot()
    assert snap[0]["role"] == "system"
    assert "第19条" in snap[-1]["content"]


def test_memory_compacts_instead_of_dropping():
    m = Memory(max_tokens=40)
    m.context_window = 40
    m.output_budget = 8
    m.complete_fn = lambda prompt, max_tokens: SUMMARY
    m.add_system("sys")
    for i in range(12):
        m.add_user(f"用户消息编号{i} " + "背景" * 20)
        m.add_assistant(f"助手回复编号{i} " + "内容" * 20)
    snap = m.snapshot()
    assert m.session_state == SUMMARY
    assert any(m.get("role") == "system" for m in snap)
    assert len([x for x in snap if x.get("role") != "system"]) < 24


def test_truncate_tool_output():
    big = "x" * (60 * 1024)
    out = truncate_tool_output_for_context(big)
    assert "truncated" in out
    assert len(out) < len(big)


def test_render_prompt_merges_previous_state():
    text = render_compress_prompt("# Session State\n- old", "<user_message index=\"1\">\nhi\n</user_message>")
    assert "Previous Session State to merge and update:" in text
    assert "- old" in text
    assert "<user_message" in text
