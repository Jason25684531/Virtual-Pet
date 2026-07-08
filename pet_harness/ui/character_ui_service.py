from __future__ import annotations

import gc
from typing import Any

from pet_harness.character.exceptions import NoActiveCharacterError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.models.events import utc_now
from pet_harness.storage.sqlite_store import SQLiteStore

PLAYTIME_SECONDS_KEY = "ui_playtime_seconds"
LAST_PLAYED_AT_KEY = "ui_last_played_at"


def _level_for_xp(xp_total: int) -> int:
    return max(1, (max(0, xp_total) // 100) + 1)


class CharacterUiService:
    def __init__(self, router: CharacterRouter, registry: CharacterRegistry) -> None:
        self._router = router
        self._registry = registry

    def list_characters(self) -> list[dict[str, Any]]:
        return [self._summarize(profile) for profile in self._registry.list_characters()]

    def list_presets(self) -> list[dict[str, Any]]:
        return [item for item in self.list_characters() if item["is_preset"]]

    def create_from_preset(self, preset_id: str, name: str | None = None) -> dict[str, Any]:
        # Select 直接切換成 preset 本體並開始遊玩，不再複製出 {preset_id}_{n} 分身。
        # character_id 永遠維持 preset 原本的 id（如 "miku"），name 參數保留僅為
        # 相容既有 bridge 呼叫介面，實際已無作用。
        return self.switch_character(preset_id)

    def switch_character(self, character_id: str) -> dict[str, Any]:
        profile = self._router.switch_character(character_id)
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        store.set_setting(LAST_PLAYED_AT_KEY, utc_now())
        return self._summarize(profile)

    def delete_character(self, character_id: str) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        if profile.is_preset:
            raise ValueError(f"preset character cannot be deleted: {character_id}")
        gc.collect()
        self._registry.delete_character(character_id)
        return {"character_id": character_id, "deleted": True}

    def get_active_state(self) -> dict[str, Any]:
        profile = self._router.get_active_character()
        engine = self._router.get_active_engine()
        if profile is None or engine is None:
            return {"active": False}

        xp_total = engine.get_xp()
        level = engine.get_level()
        current_level_min_xp = max(0, (level - 1) * 100)
        next_level_xp = max(1, level) * 100
        span = max(1, next_level_xp - current_level_min_xp)
        progress_percent = round(min(1.0, max(0.0, (xp_total - current_level_min_xp) / span)) * 100)
        snapshot = engine.state_snapshot()

        return {
            "active": True,
            "character_id": profile.character_id,
            "name": profile.name,
            "xp_total": xp_total,
            "level": level,
            "next_level_xp": next_level_xp,
            "progress_percent": progress_percent,
            "emotion": snapshot.get("behavior_state") or "idle",
            "skills": [
                {
                    "skill_id": skill.name,
                    "display_name": skill.display_name or skill.name,
                    "description": skill.description,
                }
                for skill in engine.skills
            ],
        }

    def trigger_skill(self, skill_id: str) -> dict[str, Any]:
        profile = self._router.get_active_character()
        engine = self._router.get_active_engine()
        if profile is None or engine is None:
            raise NoActiveCharacterError("no active character to trigger a skill for")
        if skill_id not in profile.skill_config:
            raise ValueError(f"skill not available for active character: {skill_id}")
        skill = next((item for item in engine.skills if item.name == skill_id), None)
        if skill is None or not skill.triggers:
            raise ValueError(f"skill has no trigger phrase configured: {skill_id}")

        event = self._router.dispatch_event({"text": skill.triggers[0], "source": "character_ui"})
        return {
            "matched_skill": event.matched_skill,
            "behavior_id": event.behavior_id,
            "webm_key": event.webm_key,
            "xp_delta": event.xp_delta,
            "reply": event.reply,
        }

    def _summarize(self, profile: CharacterProfile) -> dict[str, Any]:
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        xp_total = int(store.get_user_progress().get("xp_total", 0))
        playtime_seconds = int(store.get_setting(PLAYTIME_SECONDS_KEY, 0) or 0)
        last_played_at = store.get_setting(LAST_PLAYED_AT_KEY)
        return {
            "character_id": profile.character_id,
            "name": profile.name,
            "is_preset": profile.is_preset,
            "xp_total": xp_total,
            "level": _level_for_xp(xp_total),
            "background_image": profile.background_image,
            "playtime_seconds": max(0, playtime_seconds),
            "last_played_at": last_played_at,
        }
