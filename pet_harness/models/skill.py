from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
        if missing:
            raise ValueError(f"missing required skill fields: {', '.join(missing)}")

    @classmethod
    def from_metadata(cls, metadata: dict[str, str], file_path: Path | None = None) -> "Skill":
        triggers = [
            item.strip().lower()
            for item in metadata.get("trigger", "").replace("|", ",").split(",")
            if item.strip()
        ]
        xp_text = metadata.get("xp_reward", "0").strip()
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
        )
        skill.validate()
        return skill

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
