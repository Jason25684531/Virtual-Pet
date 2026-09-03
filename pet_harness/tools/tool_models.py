from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from pet_harness.models.events import new_id, utc_now


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolExecutionClass(str, Enum):
    INTERNAL = "internal"
    SHELL = "shell"
    FILE_SYSTEM = "file_system"
    OS_COMMAND = "os_command"
    BROWSER = "browser"
    NETWORK = "network"


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: ToolRiskLevel
    execution_class: ToolExecutionClass
    enabled: bool = True
    xp_reward: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_level"] = self.risk_level.value
        payload["execution_class"] = self.execution_class.value
        return payload


@dataclass
class ToolRequest:
    tool_name: str
    source: str
    arguments: dict[str, Any] = field(default_factory=dict)
    confirmation_metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: new_id("tool-request"))
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    tool_name: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.error is not None:
            self.error = {
                "reason": str(self.error.get("reason", "unknown_error")),
                "message": str(self.error.get("message", self.error.get("reason", "Tool execution failed"))),
                "retryable": bool(self.error.get("retryable", False)),
                **{key: value for key, value in self.error.items() if key not in {"reason", "message", "retryable"}},
            }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SafetyCheckResult:
    allowed: bool
    reason: str
    definition: ToolDefinition | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.definition is not None:
            payload["definition"] = self.definition.to_dict()
        return payload
