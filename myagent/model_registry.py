"""模型注册表（参考 Suna internal/model/router.go）。

职责：持有多个模型配置，按 ref 创建对应的 Provider，支持多提供商切换。
对应 Suna 的 Router + ModelConfig。

为什么需要？
  现在项目只支持一个模型。有了注册表，可以配置多个模型
  （比如便宜的 deepseek 做日常、强大的 gpt-4o 做复杂任务），
  按需切换，不用改代码。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .providers import BaseProvider, OpenAICompatibleProvider


@dataclass
class ModelConfig:
    """一个模型的配置（对应 Suna 的 ModelConfig）。

    ref 是唯一标识，用于路由和切换。
    provider 决定用哪个 Provider 实现（当前支持 "openai" 兼容端点）。
    """
    ref: str                    # 唯一标识，如 "deepseek" / "gpt4o"
    model: str                  # 模型名，如 "deepseek-chat" / "gpt-4o"
    base_url: str               # OpenAI 兼容端点
    api_key: str                # 凭据
    provider: str = "openai"    # 提供商类型（当前只支持 openai 兼容）
    temperature: float = 0.7
    max_tokens: int = 1024


class ModelRegistry:
    """模型注册表（对应 Suna 的 Router）。

    持有所有已配置的模型，按 ref 返回对应的 Provider。
    """

    def __init__(self, models: List[ModelConfig]):
        self._providers: Dict[str, BaseProvider] = {}
        self._configs: Dict[str, ModelConfig] = {}
        for mc in models:
            self._configs[mc.ref] = mc
            self._providers[mc.ref] = self._create_provider(mc)

    @staticmethod
    def _create_provider(mc: ModelConfig) -> BaseProvider:
        """根据 provider 类型创建对应的 Provider（对应 Suna 的 AdapterFactory）。"""
        if mc.provider == "openai":
            return OpenAICompatibleProvider(
                model=mc.model,
                base_url=mc.base_url,
                api_key=mc.api_key,
            )
        raise ValueError(f"不支持的提供商类型: {mc.provider}")

    def get_provider(self, ref: str) -> BaseProvider:
        """按 ref 获取 Provider。"""
        if ref not in self._providers:
            raise KeyError(f"模型不存在: {ref}")
        return self._providers[ref]

    def get_config(self, ref: str) -> ModelConfig:
        return self._configs[ref]

    def list_models(self) -> List[str]:
        """列出所有可用的模型 ref。"""
        return list(self._providers.keys())

    def has(self, ref: str) -> bool:
        return ref in self._providers
