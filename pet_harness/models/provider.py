from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from pet_harness.models.events import utc_now


class ProviderType(str, Enum):
    MOCK = "mock"
    API = "api"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    LOW_SPEC = "low_spec"


@dataclass
class ProviderConfig:
    provider_type: ProviderType = ProviderType.MOCK
    base_url: str | None = None
    model_name: str | None = None
    api_key_env_var: str | None = None
    timeout_seconds: float = 15.0
    fallback_provider: ProviderType = ProviderType.LOW_SPEC
    routing_fallback_enabled: bool = False
    routing_confidence_threshold: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ProviderConfig":
        if not payload:
            return cls()
        fallback = payload.get("fallback_provider", ProviderType.LOW_SPEC.value)
        return cls(
            provider_type=ProviderType(payload.get("provider_type", ProviderType.MOCK.value)),
            base_url=payload.get("base_url"),
            model_name=payload.get("model_name"),
            api_key_env_var=payload.get("api_key_env_var"),
            timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
            fallback_provider=ProviderType(fallback),
            routing_fallback_enabled=bool(payload.get("routing_fallback_enabled", False)),
            routing_confidence_threshold=float(payload.get("routing_confidence_threshold", 0.7)),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_type"] = self.provider_type.value
        payload["fallback_provider"] = self.fallback_provider.value
        return payload


@dataclass
class ProviderStatus:
    provider_type: ProviderType = ProviderType.MOCK
    healthy: bool = True
    message: str = "mock provider ready"
    checked_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ProviderStatus":
        if not payload:
            return cls()
        return cls(
            provider_type=ProviderType(payload.get("provider_type", ProviderType.MOCK.value)),
            healthy=bool(payload.get("healthy", True)),
            message=str(payload.get("message", "")),
            checked_at=str(payload.get("checked_at") or utc_now()),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_type"] = self.provider_type.value
        return payload
