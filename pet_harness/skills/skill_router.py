from __future__ import annotations

from pet_harness.models.skill import Skill


class SkillRouter:
    def __init__(self, skills: list[Skill]) -> None:
        self.skills = skills

    def match(self, text: str) -> Skill | None:
        normalized = text.casefold()
        for skill in self.skills:
            if any(trigger.casefold() in normalized for trigger in skill.triggers):
                return skill
        return None

    def route(
        self,
        text: str,
        suggested_skill_name: str | None = None,
        suggested_confidence: float = 0.0,
        allow_fallback: bool = False,
        confidence_threshold: float = 0.7,
    ) -> tuple[Skill | None, str]:
        matched = self.match(text)
        if matched is not None:
            return matched, "deterministic"
        if (
            allow_fallback
            and suggested_skill_name
            and suggested_confidence >= confidence_threshold
        ):
            suggested = self._find_by_name(suggested_skill_name)
            if suggested is not None:
                return suggested, "provider"
        return None, "none"

    def _find_by_name(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None
