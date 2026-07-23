from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionResult:
    status: str
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
