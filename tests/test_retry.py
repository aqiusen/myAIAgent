"""Runner 重试逻辑单元测试（mock OpenAI 客户端，不碰真实 API）。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openai
import pytest

from myagent.runner import Runner, MODEL_MAX_RETRIES


class Cfg:
    model = "m"
    base_url = "http://x"
    api_key = "k"
    temperature = 0.7
    max_tokens = 100


def make_runner(client):
    class FakeProvider:
        def __init__(self, client):
            self.client = client
        def complete(self, **kwargs):
            resp = self.client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            return {"content": msg.content or "", "tool_calls": []}
    r = Runner(Cfg())
    r.provider = FakeProvider(client)  # 用假 provider 替换真实 provider
    return r


class FakeResponse:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content, "tool_calls": None})})()]


def make_fake_client(create_fn):
    """构造一个支持 client.chat.completions.create(...) 链式调用的 mock。"""
    class Completions:
        def create(self, **kwargs):
            return create_fn(**kwargs)
    class Chat:
        completions = Completions()
    class Client:
        chat = Chat()
    return Client()


def test_retry_on_network_error_then_succeed(monkeypatch):
    """网络错误应重试，重试后成功。"""
    calls = {"n": 0}

    def create_fn(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise openai.APIConnectionError(request=None)
        return FakeResponse("成功")

    r = make_runner(make_fake_client(create_fn))
    # 缩短重试间隔，避免测试等太久
    monkeypatch.setattr("myagent.runner.MODEL_RETRY_DELAY", 0)
    result = r._one_call([], [])
    assert result["content"] == "成功"
    assert calls["n"] == 2  # 失败1次 + 成功1次


def test_no_retry_on_bad_request(monkeypatch):
    """参数错误（400）不应重试，直接抛出。"""
    calls = {"n": 0}

    def create_fn(**kwargs):
        calls["n"] += 1
        import httpx
        req = httpx.Request("POST", "http://x")
        raise openai.BadRequestError(message="bad", response=httpx.Response(400, request=req), body=None)

    r = make_runner(make_fake_client(create_fn))
    monkeypatch.setattr("myagent.runner.MODEL_RETRY_DELAY", 0)
    with pytest.raises(openai.BadRequestError):
        r._one_call([], [])
    assert calls["n"] == 1  # 只调用一次，不重试


def test_retry_exhausted(monkeypatch):
    """持续网络错误，重试到最大次数后抛出。"""
    calls = {"n": 0}

    def create_fn(**kwargs):
        calls["n"] += 1
        raise openai.APIConnectionError(request=None)

    r = make_runner(make_fake_client(create_fn))
    monkeypatch.setattr("myagent.runner.MODEL_RETRY_DELAY", 0)
    with pytest.raises(openai.APIConnectionError):
        r._one_call([], [])
    assert calls["n"] == MODEL_MAX_RETRIES + 1  # 1 + 3 次重试
