import re
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
    character_id: str | None = None


_ACTION_DIRECTIVE = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


def action_command_from_directive(directive: str, **kwargs) -> ActionCommand:
    stripped = str(directive or "").strip()
    match = _ACTION_DIRECTIVE.search(stripped)
    action = (match.group("bracket") or match.group("bare")).lower() if match else ""
    text = _ACTION_DIRECTIVE.sub("", stripped)
    return ActionCommand(action, re.sub(r"\s{2,}", " ", text).strip(), **kwargs)
