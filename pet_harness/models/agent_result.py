from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AgentResult:
    reply: str
    matched_skill: str | None = None
    behavior_hint: str | None = None
    confidence: float = 0.0
    tool_request: dict[str, Any] | None = None
    raw_text: str = ""
    raw_json: dict[str, Any] | None = None
    parser_status: str = "unknown"
    provider_type: str = "mock"
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
