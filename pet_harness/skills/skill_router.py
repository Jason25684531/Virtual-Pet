from __future__ import annotations

import re
from typing import Any

from pet_harness.models.skill import Skill
from pet_harness.skills.intent_normalizer import normalize
from pet_harness.skills.semantic_skill_retriever import BaseSemanticSkillRetriever

_MUSIC_START = re.compile(r"^(?:幫我|請)?(?:播放|播歌|播|放一首|放)\s*(.+)$")
_MUSIC_LISTEN = re.compile(r"^(?:我想聽|想聽)\s*(.+)$")
_MUSIC_FOLLOW_UP = {"暫停", "繼續播放", "停止播放", "現在在播放什麼"}
_NON_MUSIC_PLAYBACK = ("影片", "video", "電影", "動畫")
_NEWS_TERMS = ("遊戲新聞", "巴哈新聞", "gnn新聞", "game news")


class SkillRouter:
    """決定性 skill 命中:正規化文字後,依最長 trigger → priority → 角色技能宣告順序解衝突。"""

    def __init__(
        self,
        skills: list[Skill],
        priorities: dict[str, int] | None = None,
        semantic_retriever: BaseSemanticSkillRetriever | None = None,
    ) -> None:
        self.skills = skills
        self._priorities = dict(priorities or {})
        self.semantic_retriever = semantic_retriever
        self.last_route_diagnostics: dict[str, Any] = {}

    @staticmethod
    def normalize(text: str) -> str:
        """Unicode NFKC casefold + 空白正規化,供輸入文字與 trigger 共用同一套規則。"""
        return normalize(text).normalized_text

    def _candidates(self, text: str, active_capabilities: set[str] | None = None) -> list[tuple[Skill, str, int]]:
        normalized_input = normalize(text)
        normalized = normalized_input.normalized_text
        active_capabilities = active_capabilities or set()
        candidates: list[tuple[Skill, str, int]] = []
        for index, skill in enumerate(self.skills):
            best_trigger: str | None = None
            for trigger in skill.triggers:
                normalized_trigger = self.normalize(trigger)
                if not normalized_trigger or normalized_trigger not in normalized:
                    continue
                if best_trigger is None or len(normalized_trigger) > len(best_trigger):
                    best_trigger = normalized_trigger
            intent_trigger = self._intent_trigger(skill, normalized_input.stripped_text, active_capabilities)
            if intent_trigger and (best_trigger is None or len(intent_trigger) > len(best_trigger)):
                best_trigger = intent_trigger
            if best_trigger is not None:
                candidates.append((skill, best_trigger, index))
        return candidates

    @staticmethod
    def _intent_trigger(skill: Skill, text: str, active_capabilities: set[str]) -> str | None:
        if skill.capability == "music":
            if text in _MUSIC_FOLLOW_UP and "music" in active_capabilities:
                return "music-follow-up"
            if any(term in text for term in _NON_MUSIC_PLAYBACK):
                return None
            if _MUSIC_START.match(text) or _MUSIC_LISTEN.match(text):
                return "music-intent"
        if skill.capability == "news" and any(term in text for term in _NEWS_TERMS):
            return "news-intent"
        return None

    def _rank(self, candidates: list[tuple[Skill, str, int]]) -> list[tuple[Skill, str, int]]:
        return sorted(
            candidates,
            key=lambda item: (-len(item[1]), -self._priorities.get(item[0].name, item[0].priority), item[2]),
        )

    def match(self, text: str, active_capabilities: set[str] | None = None) -> Skill | None:
        candidates = self._candidates(text, active_capabilities)
        if not candidates:
            return None
        return self._rank(candidates)[0][0]

    def match_diagnostics(self, text: str, active_capabilities: set[str] | None = None) -> dict[str, Any]:
        """非執行預覽用:回傳選中 skill、命中 trigger 與完整候選排序,不觸發任何行為。"""
        candidates = self._candidates(text, active_capabilities)
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
        active_capabilities: set[str] | None = None,
        semantic_enabled: bool = False,
        semantic_shadow_mode: bool = True,
        semantic_top_k: int = 3,
        semantic_accept_threshold: float = 0.6,
        semantic_margin_threshold: float = 0.08,
    ) -> tuple[Skill | None, str]:
        normalized = normalize(text)
        self.last_route_diagnostics = {
            "normalized_query": normalized.normalized_text,
            "stripped_query": normalized.stripped_text,
            "route_source": "none",
            "rejection_reason": None,
        }
        matched = self.match(text, active_capabilities)
        if matched is not None:
            self.last_route_diagnostics.update(route_source="deterministic", selected_skill=matched.name)
            return matched, "deterministic"
        if semantic_enabled and self.semantic_retriever is not None:
            status = self.semantic_retriever.status()
            if status.state != "ready":
                self.last_route_diagnostics["rejection_reason"] = "not_indexed"
                self.last_route_diagnostics["semantic_status"] = status.state
            else:
                candidates = self.semantic_retriever.search(normalized.normalized_text, semantic_top_k)
                self.last_route_diagnostics["semantic_candidates"] = [
                    {"skill_id": item.skill_id, "score": item.score} for item in candidates
                ]
                selected = candidates[0] if candidates else None
                margin = selected.score - candidates[1].score if selected and len(candidates) > 1 else float("inf")
                skill = self._find_by_name(selected.skill_id) if selected else None
                accepted = bool(selected and skill and selected.score >= semantic_accept_threshold and margin >= semantic_margin_threshold)
                if selected:
                    self.last_route_diagnostics["semantic_margin"] = margin
                if semantic_shadow_mode:
                    self.last_route_diagnostics["semantic_shadow"] = {
                        "candidates": self.last_route_diagnostics["semantic_candidates"],
                        "would_have_selected": skill.name if accepted else None,
                    }
                elif accepted:
                    self.last_route_diagnostics.update(route_source="semantic", selected_skill=skill.name)
                    return skill, "semantic"
                elif selected:
                    self.last_route_diagnostics["rejection_reason"] = "semantic_rejected"
        if (
            allow_fallback
            and suggested_skill_name
            and suggested_confidence >= confidence_threshold
        ):
            suggested = self._find_by_name(suggested_skill_name)
            if suggested is not None:
                self.last_route_diagnostics.update(route_source="provider", selected_skill=suggested.name)
                return suggested, "provider"
            self.last_route_diagnostics["rejection_reason"] = "unknown_skill_id"
        self.last_route_diagnostics["provider_suggestion"] = suggested_skill_name
        self.last_route_diagnostics["provider_confidence"] = suggested_confidence
        return None, "none"

    def _find_by_name(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None
