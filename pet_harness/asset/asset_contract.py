from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pet_harness.models.events import new_id, utc_now


@dataclass
class AssetRequest:
    asset_type: str
    prompt_params: dict[str, Any]
    source_event_id: str
    request_id: str = field(default_factory=lambda: new_id("asset-request"))
    requested_reward: str | None = None
    behavior_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GrowthOffer:
    variant: str
    reason: str
    source_event_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AssetResponse:
    request_id: str
    status: str
    asset_id: str | None = None
    job_id: str | None = None
    file_path: str | None = None
    webm_key: str | None = None
    error_message: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["completed_at"] is None and self.status == "completed":
            payload["completed_at"] = utc_now()
        return payload
