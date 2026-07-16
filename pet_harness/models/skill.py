from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    behavior: str
    xp_reward: int
    display_name: str | None = None
    required_tool: str | None = None
    unlock_reward: str | None = None
    file_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_policy: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    capability: str = "general"

    def validate(self) -> None:
        missing = []
        if not self.name:
            missing.append("name")
        if not self.description:
            missing.append("description")
        if not self.triggers:
            missing.append("trigger")
        if not self.behavior:
            missing.append("behavior")
        if self.xp_reward < 0:
            missing.append("xp_reward")
        if self.priority < 0:
            missing.append("priority")
        if missing:
            raise ValueError(f"missing required skill fields: {', '.join(missing)}")
        if self.tool_policy:
            actions = self.tool_policy.get("allowed_actions")
            domains = self.tool_policy.get("allowed_domains")
            if not isinstance(actions, list) or not all(isinstance(value, str) and value for value in actions):
                raise ValueError("tool policy requires allowed_actions")
            if not isinstance(domains, list) or not all(isinstance(value, str) and value for value in domains):
                raise ValueError("tool policy requires allowed_domains")
            forbidden = {"selector", "xpath", "javascript", "js"}
            if any(forbidden.intersection(map(str.lower, value.keys())) for value in _walk_dicts(self.tool_policy)):
                raise ValueError("tool policy contains forbidden browser controls")

    @classmethod
    def from_metadata(cls, metadata: dict[str, str], file_path: Path | None = None) -> "Skill":
        triggers = [
            item.strip().lower()
            for item in metadata.get("trigger", "").replace("|", ",").split(",")
            if item.strip()
        ]
        xp_text = metadata.get("xp_reward", "0").strip()
        raw_policy = metadata.get("tool_policy_json", "").strip()
        try:
            tool_policy = json.loads(raw_policy) if raw_policy else {}
        except json.JSONDecodeError as exc:
            raise ValueError("tool_policy_json is invalid JSON") from exc
        priority_text = metadata.get("priority", "").strip()
        priority_value = priority_text or str(tool_policy.get("priority", 0))
        try:
            priority = int(priority_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("priority must be a non-negative integer") from exc
        capability = metadata.get("capability", "").strip().lower()
        if not capability:
            capability = str(tool_policy.get("capability") or _infer_capability(metadata.get("required_tool", ""))).lower()
        skill = cls(
            name=metadata.get("name", "").strip(),
            description=metadata.get("description", "").strip(),
            triggers=triggers,
            behavior=metadata.get("behavior", "").strip(),
            xp_reward=int(xp_text) if xp_text.isdigit() else -1,
            display_name=metadata.get("display_name", "").strip() or None,
            required_tool=metadata.get("required_tool", "").strip() or None,
            unlock_reward=metadata.get("unlock_reward", "").strip() or None,
            file_path=str(file_path) if file_path else None,
            tool_policy=tool_policy,
            priority=priority,
            capability=capability or "general",
        )
        skill.validate()
        return skill

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _infer_capability(required_tool: str) -> str:
    return {
        "youtube_music_tool": "music",
        "web_article_tool": "news",
    }.get(required_tool, "general")
