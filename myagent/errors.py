"""模型错误分类（参考 Suna internal/model/error.go）。

职责：把 OpenAI SDK 抛出的各种异常，统一分类成 ModelError，
让上层（runner 的重试逻辑）能判断"这个错误该不该重试"。

对应 Suna 的 ModelErrorKind：
  - http      HTTP 错误（带状态码，如 429 限流、500 服务器错误）
  - network   网络错误（连接失败、超时）
  - cancelled 用户取消
  - unknown   未知错误

为什么需要分类？
  不是所有错误都该重试。网络抖动、限流、5xx 可以重试；
  参数错误（400）、鉴权失败（401）、权限不足（403）重试也没用，反而浪费。
"""
from typing import Optional

import openai


class ModelErrorKind:
    """错误类型枚举（对应 Suna 的 ModelErrorKind）。"""
    UNKNOWN = "unknown"
    HTTP = "http"
    NETWORK = "network"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class ModelError(Exception):
    """统一模型错误（对应 Suna 的 ModelError）。"""

    def __init__(
        self,
        kind: str,
        message: str,
        status_code: int = 0,
        code: str = "",
        type_: str = "",
    ):
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.code = code
        self.type = type_
        super().__init__(message)

    def __str__(self) -> str:
        if self.status_code > 0:
            return f"HTTP {self.status_code} {self.message}"
        return self.message


def classify_error(err: Exception) -> ModelError:
    """把异常分类成 ModelError（对应 Suna 的 modelErrorFromProvider）。

    分类规则（参考 Suna）：
      - 用户取消 → cancelled
      - 网络/超时 → network
      - OpenAI 的 HTTP 状态错误 → http（带状态码）
      - 其他 → unknown
    """
    # 已经是 ModelError，直接返回
    if isinstance(err, ModelError):
        return err

    # 用户取消（KeyboardInterrupt / 取消）
    if isinstance(err, (KeyboardInterrupt,)):
        return ModelError(ModelErrorKind.CANCELLED, "cancelled")

    # 网络错误：连接失败、超时
    if isinstance(err, (openai.APIConnectionError, openai.APITimeoutError)):
        return ModelError(ModelErrorKind.NETWORK, str(err))

    # OpenAI 的 HTTP 状态错误（带 status_code）
    if isinstance(err, openai.APIStatusError):
        return ModelError(
            ModelErrorKind.HTTP,
            err.message or str(err),
            status_code=err.status_code,
            code=getattr(err, "code", "") or "",
        )

    # 其他 OpenAI API 错误
    if isinstance(err, openai.APIError):
        return ModelError(ModelErrorKind.HTTP, str(err))

    # 未知错误
    return ModelError(ModelErrorKind.UNKNOWN, str(err))


def retryable(err: ModelError) -> bool:
    """判断错误是否值得重试（对应 Suna 的 retryableModelRequestError）。

    规则（参考 Suna）：
      - 网络错误 → 重试（抖动、超时）
      - HTTP 错误 → 只重试 408/429/500/502/503/504（限流、服务器临时故障）
      - 其他（参数错误、鉴权失败、未知）→ 不重试
    """
    if err is None:
        return False
    if err.kind == ModelErrorKind.CANCELLED:
        return False
    if err.kind == ModelErrorKind.NETWORK:
        return True
    if err.kind != ModelErrorKind.HTTP:
        return False
    return err.status_code in {408, 429, 500, 502, 503, 504}
