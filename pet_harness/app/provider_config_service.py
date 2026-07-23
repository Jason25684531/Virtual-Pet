from __future__ import annotations

import os
from typing import Any

import config
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.runtime.provider_runtime import ProviderRuntime


class ProviderConfigService:
    def __init__(self, runtime: ProviderRuntime, environment: dict[str, str], *, api_url: str, ollama_model: str) -> None:
        self._runtime, self._environment = runtime, environment
        self._api_url, self._ollama_model = api_url, ollama_model

    def build(self, provider: str) -> ProviderConfig:
        provider_type = ProviderType(str(provider))
        common = {
            "routing_fallback_enabled": config.PROVIDER_ROUTING_FALLBACK_ENABLED,
            "routing_confidence_threshold": config.PROVIDER_ROUTING_CONFIDENCE_THRESHOLD,
        }
        if provider_type is ProviderType.API:
            return ProviderConfig(
                provider_type=provider_type,
                base_url=self._environment.get("ECHOES_API_BASE_URL") or self._environment.get("OPENAI_BASE_URL") or self._api_url,
                model_name=self._environment.get("ECHOES_API_MODEL") or self._environment.get("OPENAI_MODEL") or "gpt-4o-mini",
                api_key_env_var=next((key for key in ("ECHOES_API_KEY", "OPENAI_API_KEY", "CHATGPT_API_KEY") if self._environment.get(key) or os.environ.get(key)), "OPENAI_API_KEY"),
                **common,
            )
        return ProviderConfig(provider_type=ProviderType.OLLAMA, base_url=self._environment.get("OLLAMA_BASE_URL") or "http://localhost:11434", model_name=self._environment.get("OLLAMA_MODEL") or self._ollama_model, api_key_env_var=None, timeout_seconds=60.0, **common)

    def configure(self, provider: str) -> dict[str, Any]:
        return self._runtime.configure(self.build(provider)).to_dict()

    def bootstrap(self) -> None:
        if self._runtime.is_test_injected:
            return
        if self._runtime.get_config() is not None:
            self._runtime.refresh_status()
        else:
            self._runtime.configure(self.build("ollama"))
