"""错误分类（errors.py）单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openai

from myagent.errors import (
    ModelError,
    ModelErrorKind,
    classify_error,
    retryable,
)


def test_classify_network_error():
    err = openai.APIConnectionError(request=None)
    me = classify_error(err)
    assert me.kind == ModelErrorKind.NETWORK


def test_classify_timeout_error():
    err = openai.APITimeoutError(request=None)
    me = classify_error(err)
    assert me.kind == ModelErrorKind.NETWORK


def test_classify_http_status_error():
    # RateLimitError 需要真实的 httpx.Response
    import httpx
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(429, request=req)
    err = openai.RateLimitError(message="rate limited", response=resp, body=None)
    me = classify_error(err)
    assert me.kind == ModelErrorKind.HTTP
    assert me.status_code == 429


def test_classify_unknown_error():
    me = classify_error(ValueError("something"))
    assert me.kind == ModelErrorKind.UNKNOWN


def test_classify_cancelled():
    me = classify_error(KeyboardInterrupt())
    assert me.kind == ModelErrorKind.CANCELLED


def test_retryable_network():
    me = ModelError(ModelErrorKind.NETWORK, "timeout")
    assert retryable(me) is True


def test_retryable_http_429():
    me = ModelError(ModelErrorKind.HTTP, "rate limit", status_code=429)
    assert retryable(me) is True


def test_retryable_http_500():
    me = ModelError(ModelErrorKind.HTTP, "server error", status_code=500)
    assert retryable(me) is True


def test_not_retryable_http_400():
    me = ModelError(ModelErrorKind.HTTP, "bad request", status_code=400)
    assert retryable(me) is False


def test_not_retryable_http_401():
    me = ModelError(ModelErrorKind.HTTP, "auth failed", status_code=401)
    assert retryable(me) is False


def test_not_retryable_unknown():
    me = ModelError(ModelErrorKind.UNKNOWN, "unknown")
    assert retryable(me) is False


def test_not_retryable_cancelled():
    me = ModelError(ModelErrorKind.CANCELLED, "cancelled")
    assert retryable(me) is False
