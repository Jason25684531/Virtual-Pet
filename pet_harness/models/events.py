from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _json_ready(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


@dataclass
class UserEvent:
    text: str
    source: str = "debug_cli"
    event_type: str = "text"
    event_id: str = field(default_factory=lambda: new_id("user"))
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserEvent":
        return cls(
            text=str(payload.get("text", "")),
            source=str(payload.get("source", "debug_cli")),
            event_type=str(payload.get("event_type", "text")),
            event_id=str(payload.get("event_id") or new_id("user")),
            timestamp=str(payload.get("timestamp") or utc_now()),
            metadata=dict(payload.get("metadata") or {}),
            session_id=payload.get("session_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BehaviorEvent:
    behavior_id: str
    webm_key: str
    reason: str = "fallback"
    source_skill: str | None = None
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RewardEvent:
    reward_id: str
    reward_type: str
    unlock_reason: str
    xp_threshold: int
    inventory_item_id: str
    timestamp: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolRequestEvent:
    tool_name: str
    source_skill: str
    status: str = "pending"
    event_id: str = field(default_factory=lambda: new_id("tool"))
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PetEvent:
    source_event_id: str
    reply: str
    matched_skill: str | None
    behavior_id: str
    webm_key: str
    xp_delta: int
    provider_status: dict[str, Any]
    saved_to_db: bool
    action_tag: str | None = None
    motion_source: str = "fallback"
    reward_events: list[RewardEvent | dict[str, Any]] = field(default_factory=list)
    tool_request: ToolRequestEvent | dict[str, Any] | None = None
    event_id: str = field(default_factory=lambda: new_id("pet"))
    timestamp: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reward_events"] = _json_ready(self.reward_events)
        payload["tool_request"] = _json_ready(self.tool_request)
        payload["provider_status"] = _json_ready(self.provider_status)
        return payload
