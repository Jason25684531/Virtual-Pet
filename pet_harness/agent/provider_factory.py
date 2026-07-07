from __future__ import annotations

from typing import Any, Callable

from pet_harness.agent.api_provider import APIProvider
from pet_harness.agent.low_spec_provider import LowSpecProvider
from pet_harness.agent.mock_provider import MockProvider
from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.models.provider import ProviderConfig, ProviderType


def create_provider(
    config: ProviderConfig,
    request_fn: Callable[..., Any] | None = None,
):
    if config.provider_type is ProviderType.API:
        return APIProvider(config, request_fn=request_fn)
    if config.provider_type is ProviderType.OLLAMA:
        return OllamaProvider(config, request_fn=request_fn)
    if config.provider_type is ProviderType.LOW_SPEC:
        return LowSpecProvider()
    return MockProvider()
