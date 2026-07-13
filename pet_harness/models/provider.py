from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from pet_harness.models.events import utc_now


class ProviderType(str, Enum):
    """產品 runtime 僅支援 api 與 ollama;mock/low_spec 已移除,測試請注入 fake adapter。"""

    API = "api"
    OLLAMA = "ollama"


@dataclass
class ProviderConfig:
    provider_type: ProviderType
    base_url: str | None = None
    model_name: str | None = None
    api_key_env_var: str | None = None
    timeout_seconds: float = 15.0
    routing_fallback_enabled: bool = False
    routing_confidence_threshold: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ProviderConfig":
        if not payload or not payload.get("provider_type"):
            raise ValueError("provider config requires provider_type ('api' or 'ollama')")
        return cls(
            provider_type=ProviderType(payload["provider_type"]),
            base_url=payload.get("base_url"),
            model_name=payload.get("model_name"),
            api_key_env_var=payload.get("api_key_env_var"),
            timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
            routing_fallback_enabled=bool(payload.get("routing_fallback_enabled", False)),
            routing_confidence_threshold=float(payload.get("routing_confidence_threshold", 0.7)),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_type"] = self.provider_type.value
        return payload


@dataclass
class ProviderStatus:
    # provider_type=None 表示尚未設定任何 Provider(unconfigured)。
    provider_type: ProviderType | None = None
    healthy: bool = False
    message: str = "provider not configured"
    checked_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ProviderStatus":
        if not payload:
            return cls()
        raw_type = payload.get("provider_type")
        return cls(
            provider_type=ProviderType(raw_type) if raw_type else None,
            healthy=bool(payload.get("healthy", False)),
            message=str(payload.get("message", "")),
            checked_at=str(payload.get("checked_at") or utc_now()),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_type"] = self.provider_type.value if self.provider_type else None
        return payload
