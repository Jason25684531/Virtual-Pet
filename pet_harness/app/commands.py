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


@dataclass(frozen=True)
class AppEvent:
    name: str
    trace_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    status: str
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


def action_command_from_directive(directive: str, **kwargs) -> ActionCommand:
    stripped = str(directive or "").strip()
    match = ACTION_DIRECTIVE_PATTERN.search(stripped)
    action = (match.group("bracket") or match.group("bare")).lower() if match else ""
    text = ACTION_DIRECTIVE_PATTERN.sub("", stripped)
    return ActionCommand(action, re.sub(r"\s{2,}", " ", text).strip(), **kwargs)
