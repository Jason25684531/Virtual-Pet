from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionCommand:
    action: str
    text: str = ""
    trace_id: str | None = None
    source: str = "ui"
    allow_tts: bool = True
    wait_for_tts_start: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
