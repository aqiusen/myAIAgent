"""阶段 1.2 流式输出的单元测试（用 mock 模拟 OpenAI 流式响应，不碰真实 API）。"""
import types
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.runner import Runner


class Cfg:
    model = "m"
    base_url = "http://x"
    api_key = "k"
    temperature = 0.7
    max_tokens = 100


def make_chunk(content=None, tc=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tc)
    choice = types.SimpleNamespace(delta=delta)
    return types.SimpleNamespace(choices=[choice])


def fake_stream():
    # 工具调用分片 1：index 0，名字 read_file，arguments 只来了一半
    yield make_chunk(
        tc=[types.SimpleNamespace(
            index=0, id="call_1",
            function=types.SimpleNamespace(name="read_file", arguments='{"path"'),
        )]
    )
    # 工具调用分片 2：同一 index，补全 arguments 后半段
    yield make_chunk(
        tc=[types.SimpleNamespace(
            index=0, id=None,
            function=types.SimpleNamespace(name=None, arguments=': "main.py"}'),
        )]
    )
    # 最终答案文字分片
    yield make_chunk(content="这是")
    yield make_chunk(content="最终答案")


def test_stream_accumulates_content_and_tool_calls():
    r = Runner(Cfg())
    r.client.chat.completions.create = lambda **kw: fake_stream()

    collected = []
    result = r._one_call([], [], on_delta=collected.append)

    assert result["content"] == "这是最终答案"
    assert "".join(collected) == "这是最终答案"  # 流式回调收到完整文字
    assert result["tool_calls"][0]["id"] == "call_1"
    assert result["tool_calls"][0]["function"]["name"] == "read_file"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"path": "main.py"}'
    print("流式累加逻辑 OK")


if __name__ == "__main__":
    test_stream_accumulates_content_and_tool_calls()
