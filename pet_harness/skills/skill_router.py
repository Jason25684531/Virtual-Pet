from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pet_harness.models.skill import Skill
from pet_harness.skills.intent_normalizer import normalize
from pet_harness.skills.semantic_skill_retriever import BaseSemanticSkillRetriever

_MEDIA_CAPABILITIES = {"news", "music"}
_NON_MUSIC_PLAYBACK = ("影片", "video", "電影", "動畫")
# 「別」要排除 特別／分別／類別 這類複合詞,否則「特別想聽稻香」會被當成否定。
_NEGATION = re.compile(r"不要|不用|不想|不需要|(?<![特分個性類差告級識])別|拒絕|don't|do not|no need")
_NEWS_WORDS = re.compile(r"新聞|頭條|快報|gnn|巴哈|news")
_MUSIC_WORDS = re.compile(r"音樂|歌曲|歌|music|song|bgm|playlist|soundtrack")
_MUSIC_CONTROL = re.compile(r"暫停|繼續播放|停止播放|停止|音量|現在在播放什麼|pause|resume|stop|volume")
_PLAY_VERB = re.compile(r"^(?:播放|播報|播歌|播|放一首|放|play|put on)\s*(.*)$")
_LISTEN_VERB = re.compile(r"^(?:我想聽|想聽|listen to)\s*(.*)$")
# 澄清/否決狀態下一律不執行媒體技能:媒體動作有外部副作用,置信度不足時 fail-closed。
_MEDIA_BLOCKING_REASONS = {"negated", "conflict", "missing_music_query", "no_media_session"}


@dataclass(frozen=True)
class MediaIntent:
    """媒體意圖的決定性判定結果。

    capability 是使用者要求的能力(news/music),reason 說明為何可以或不能執行:
    matched/control 代表可執行,其餘皆代表要澄清或忽略,絕不猜一個開始播放。
    """

    capability: str | None = None
    reason: str = "none"

    @property
    def blocks_media(self) -> bool:
        return self.reason in _MEDIA_BLOCKING_REASONS

    def to_dict(self) -> dict[str, Any]:
        return {"media_capability": self.capability, "media_reason": self.reason}


_MEDIA_INTENT_TRIGGER = "media-intent"


def _trigger_weight(trigger: str) -> int:
    """字面 trigger 永遠比意圖推導優先:同能力內「巴哈新聞」比泛用新聞意圖更明確。

    內部標籤的字串長度不得參與排序,那正是「播放遊戲新聞」被音樂搶走的成因。
    """
    return 0 if trigger == _MEDIA_INTENT_TRIGGER else len(trigger)


def _verb_object(text: str) -> str | None:
    for pattern in (_PLAY_VERB, _LISTEN_VERB):
        match = pattern.match(text)
        if match:
            return match.group(1).strip()
    return None


def resolve_media_intent(text: str, active_capabilities: set[str] | None = None) -> MediaIntent:
    """依「否定 → 衝突 → 能力 → 必要參數」的順序判定媒體意圖(text 須為 stripped_text)。

    刻意不使用內部 intent 標籤的長度解衝突:「播放遊戲新聞」的受詞是新聞,
    播放動詞不該讓音樂技能搶走;而「不要播放音樂」有字面 trigger 也不得執行。
    """
    active = active_capabilities or set()
    blocked_playback = any(term in text for term in _NON_MUSIC_PLAYBACK)
    news = bool(_NEWS_WORDS.search(text))
    music_word = bool(_MUSIC_WORDS.search(text)) and not blocked_playback
    control = bool(_MUSIC_CONTROL.search(text)) and not blocked_playback
    verb_object = None if blocked_playback else _verb_object(text)
    # 受詞是新聞時,播放動詞不構成音樂意圖;單純聊到音樂(沒有動作)也不算。
    verb_music = bool(verb_object) and not _NEWS_WORDS.search(verb_object)
    music = control or verb_music or (music_word and bool(verb_object))

    # 否定要先判:「不要播放音樂」沒有可執行的意圖,卻有字面 trigger,
    # 放到意圖成立之後才檢查等於沒檢查。
    if _NEGATION.search(text) and (news or music_word or music):
        return MediaIntent(None, "negated")
    if not news and not music:
        return MediaIntent()
    # 兩個能力都被明確點名就是沒有順序的混合請求,不猜一個開始播放。
    if news and music_word:
        return MediaIntent(None, "conflict")
    if news:
        return MediaIntent("news", "matched")
    if control and not verb_music:
        return MediaIntent("music", "control" if "music" in active else "no_media_session")
    query = _MUSIC_WORDS.sub("", verb_object or "").strip().strip("的 ")
    return MediaIntent("music", "matched" if query else "missing_music_query")


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
        self.last_media_intent = MediaIntent()

    @staticmethod
    def normalize(text: str) -> str:
        """Unicode NFKC casefold + 空白正規化,供輸入文字與 trigger 共用同一套規則。"""
        return normalize(text).normalized_text

    def _candidates(self, text: str, active_capabilities: set[str] | None = None) -> list[tuple[Skill, str, int]]:
        normalized_input = normalize(text)
        normalized = normalized_input.normalized_text
        active_capabilities = active_capabilities or set()
        intent = resolve_media_intent(normalized_input.stripped_text, active_capabilities)
        self.last_media_intent = intent
        candidates: list[tuple[Skill, str, int]] = []
        for index, skill in enumerate(self.skills):
            best_trigger: str | None = None
            for trigger in skill.triggers:
                normalized_trigger = self.normalize(trigger)
                if not normalized_trigger or normalized_trigger not in normalized:
                    continue
                if best_trigger is None or len(normalized_trigger) > len(best_trigger):
                    best_trigger = normalized_trigger
            if skill.capability in _MEDIA_CAPABILITIES:
                best_trigger = self._media_trigger(skill, intent, best_trigger)
            if best_trigger is not None:
                candidates.append((skill, best_trigger, index))
        return candidates

    @staticmethod
    def _media_trigger(skill: Skill, intent: MediaIntent, literal_trigger: str | None) -> str | None:
        """媒體技能的最終否決權在意圖判定,不在字面 trigger。

        否定、跨能力衝突、缺必要參數或沒有有效工作階段時,即使字面 trigger 命中也
        不得執行;意圖明確時則補上 media-intent,讓「新聞」「播報新聞」這類沒有字面
        trigger 的說法也能命中正確能力。
        """
        if intent.blocks_media:
            return None
        if intent.capability is None:
            return literal_trigger
        if intent.capability != skill.capability:
            return None
        return literal_trigger or _MEDIA_INTENT_TRIGGER

    def _rank(self, candidates: list[tuple[Skill, str, int]]) -> list[tuple[Skill, str, int]]:
        return sorted(
            candidates,
            key=lambda item: (-_trigger_weight(item[1]), -self._priorities.get(item[0].name, item[0].priority), item[2]),
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
        self.last_route_diagnostics.update(self.last_media_intent.to_dict())
        if self.last_media_intent.blocks_media:
            self.last_route_diagnostics["rejection_reason"] = self.last_media_intent.reason
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
                accepted = bool(
                    selected
                    and self._media_allowed(skill)
                    and selected.score >= semantic_accept_threshold
                    and margin >= semantic_margin_threshold
                )
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
            if suggested is not None and self._media_allowed(suggested):
                self.last_route_diagnostics.update(route_source="provider", selected_skill=suggested.name)
                return suggested, "provider"
            if suggested is None:
                self.last_route_diagnostics["rejection_reason"] = "unknown_skill_id"
        self.last_route_diagnostics["provider_suggestion"] = suggested_skill_name
        self.last_route_diagnostics["provider_confidence"] = suggested_confidence
        return None, "none"

    def _media_allowed(self, skill: Skill | None) -> bool:
        """否定/衝突/缺參數的回合,semantic 與 provider fallback 也不得繞過 deterministic 判定。"""
        if skill is None:
            return False
        return not (self.last_media_intent.blocks_media and skill.capability in _MEDIA_CAPABILITIES)

    def _find_by_name(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None
