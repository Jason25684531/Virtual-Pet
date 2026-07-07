from __future__ import annotations

import gc
from typing import Any

from pet_harness.character.exceptions import CharacterNotFoundError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.storage.sqlite_store import SQLiteStore


def _level_for_xp(xp_total: int) -> int:
    return max(1, (max(0, xp_total) // 100) + 1)


class CharacterUiService:
    """UI 與 pet_harness 之間的角色管理橋接核心（純 Python，無 Qt 依賴）。

    全數委派給既有 CharacterRegistry / CharacterRouter，本身不重寫任何 CRUD 邏輯。
    """

    def __init__(self, router: CharacterRouter, registry: CharacterRegistry) -> None:
        self._router = router
        self._registry = registry

    def list_characters(self) -> list[dict[str, Any]]:
        return [self._summarize(profile) for profile in self._registry.list_characters()]

    def list_presets(self) -> list[dict[str, Any]]:
        return [item for item in self.list_characters() if item["is_preset"]]

    def create_from_preset(self, preset_id: str, name: str | None = None) -> dict[str, Any]:
        preset = self._registry.load_character(preset_id)
        new_id = self._next_available_id(preset_id)

        self._registry.create_character(
            character_id=new_id,
            name=name or preset.name,
            persona_description=preset.persona_description,
            skill_config=list(preset.skill_config),
            voice_id_env_key=preset.voice_id_env_key,
            layout=dict(preset.layout),
        )
        self._registry.update_manifest(
            new_id,
            {
                "motions_dir": preset.motions_dir,
                "motions": dict(preset.motions),
                "idle_pool": list(preset.idle_pool),
                "background_image": preset.background_image,
            },
        )

        profile = self._router.switch_character(new_id)
        return self._summarize(profile)

    def switch_character(self, character_id: str) -> dict[str, Any]:
        profile = self._router.switch_character(character_id)
        return self._summarize(profile)

    def delete_character(self, character_id: str) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        if profile.is_preset:
            raise ValueError(f"preset character cannot be deleted: {character_id}")
        # Windows 上剛開過的 SQLite 連線可能形成參考循環，rmtree 前先強制 GC
        # 釋放檔案鎖，避免 state.db 刪除被靜默忽略而殘留目錄。
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

    def _summarize(self, profile: CharacterProfile) -> dict[str, Any]:
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        xp_total = int(store.get_user_progress().get("xp_total", 0))
        return {
            "character_id": profile.character_id,
            "name": profile.name,
            "is_preset": profile.is_preset,
            "xp_total": xp_total,
            "level": _level_for_xp(xp_total),
        }

    def _next_available_id(self, preset_id: str) -> str:
        existing_ids = {item["character_id"] for item in self.list_characters()}
        index = 1
        while f"{preset_id}_{index}" in existing_ids:
            index += 1
        return f"{preset_id}_{index}"
