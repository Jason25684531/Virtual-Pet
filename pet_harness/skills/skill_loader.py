from __future__ import annotations

import logging
from pathlib import Path

from pet_harness.models.skill import Skill

LOGGER = logging.getLogger(__name__)


class SkillLoader:
    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)
        self.load_errors: dict[str, str] = {}

    def load_skills(self) -> list[Skill]:
        if not self.skills_dir.exists():
            LOGGER.warning("Skill directory does not exist: %s", self.skills_dir)
            return []

        skills: list[Skill] = []
        for path in sorted(self.skills_dir.rglob("*.md")):
            try:
                metadata = self._parse_metadata(path)
                skills.append(Skill.from_metadata(metadata, file_path=path))
            except Exception as exc:  # noqa: BLE001 - bad skill files must degrade safely.
                LOGGER.warning("Skipping invalid skill %s: %s", path, exc)
                self.load_errors[str(path)] = str(exc)
        return skills

    def _parse_metadata(self, path: Path) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            normalized = key.strip().lower()
            if normalized in {
                "name",
                "display_name",
                "description",
                "trigger",
                "behavior",
                "xp_reward",
                "required_tool",
                "unlock_reward",
                "tool_policy_json",
                "priority",
                "capability",
                "slow_tool",
                "ack_template",
                "post_tool_response_policy",
            }:
                metadata[normalized] = value.strip()
        return metadata
