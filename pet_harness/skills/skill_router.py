from __future__ import annotations

import re
import unicodedata
from typing import Any

from pet_harness.models.skill import Skill

_WHITESPACE_PATTERN = re.compile(r"\s+")


class SkillRouter:
    """決定性 skill 命中:正規化文字後,依最長 trigger → priority → 角色技能宣告順序解衝突。"""

    def __init__(self, skills: list[Skill], priorities: dict[str, int] | None = None) -> None:
        self.skills = skills
        self._priorities = dict(priorities or {})

    @staticmethod
    def normalize(text: str) -> str:
        """Unicode NFKC casefold + 空白正規化,供輸入文字與 trigger 共用同一套規則。"""
        folded = unicodedata.normalize("NFKC", text or "").casefold()
        return _WHITESPACE_PATTERN.sub(" ", folded).strip()

    def _candidates(self, text: str) -> list[tuple[Skill, str, int]]:
        normalized = self.normalize(text)
        candidates: list[tuple[Skill, str, int]] = []
        for index, skill in enumerate(self.skills):
            best_trigger: str | None = None
            for trigger in skill.triggers:
                normalized_trigger = self.normalize(trigger)
                if not normalized_trigger or normalized_trigger not in normalized:
                    continue
                if best_trigger is None or len(normalized_trigger) > len(best_trigger):
                    best_trigger = normalized_trigger
            if best_trigger is not None:
                candidates.append((skill, best_trigger, index))
        return candidates

    def _rank(self, candidates: list[tuple[Skill, str, int]]) -> list[tuple[Skill, str, int]]:
        return sorted(
            candidates,
            key=lambda item: (-len(item[1]), -self._priorities.get(item[0].name, 0), item[2]),
        )

    def match(self, text: str) -> Skill | None:
        candidates = self._candidates(text)
        if not candidates:
            return None
        return self._rank(candidates)[0][0]

    def match_diagnostics(self, text: str) -> dict[str, Any]:
        """非執行預覽用:回傳選中 skill、命中 trigger 與完整候選排序,不觸發任何行為。"""
        candidates = self._candidates(text)
        if not candidates:
            return {"matched": False, "skill_id": None, "trigger": None, "candidates": []}
        ranked = self._rank(candidates)
        return {
            "matched": True,
            "skill_id": ranked[0][0].name,
            "trigger": ranked[0][1],
            "candidates": [{"skill_id": skill.name, "trigger": trigger} for skill, trigger, _ in ranked],
        }

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
