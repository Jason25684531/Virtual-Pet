from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RewardRule:
    reward_id: str
    reward_type: str
    xp_threshold: int
    inventory_item_id: str
    unlock_reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RewardRule":
        return cls(
            reward_id=str(payload["reward_id"]),
            reward_type=str(payload.get("reward_type", "badge")),
            xp_threshold=int(payload.get("xp_threshold", 0)),
            inventory_item_id=str(payload.get("inventory_item_id") or payload["reward_id"]),
            unlock_reason=str(payload.get("unlock_reason", "xp_threshold")),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
