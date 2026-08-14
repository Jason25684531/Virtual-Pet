from __future__ import annotations

import gc
import random
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from character_library import CharacterLibrary
from pet_harness.asset.asset_contract import AssetRequest
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.factory import build_asset_service
from pet_harness.character.customization_service import CharacterCustomizationService
from pet_harness.character.exceptions import NoActiveCharacterError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.models.events import utc_now
from pet_harness.storage.sqlite_store import SQLiteStore

PLAYTIME_SECONDS_KEY = "ui_playtime_seconds"
LAST_PLAYED_AT_KEY = "ui_last_played_at"
FESTIVAL_PROMPT_HISTORY_KEY = "asset_event_prompt_history"


def _level_for_xp(xp_total: int) -> int:
    return max(1, (max(0, xp_total) // 100) + 1)


class CharacterUiService:
    def __init__(
        self,
        router: CharacterRouter,
        registry: CharacterRegistry,
        customization_service: CharacterCustomizationService | None = None,
    ) -> None:
        self._router = router
        self._registry = registry
        self._customization = customization_service or CharacterCustomizationService(
            registry=registry, router=router
        )

    def list_characters(self) -> list[dict[str, Any]]:
        items = {profile.character_id: self._summarize(profile) for profile in self._registry.list_characters()}
        # 上傳生成的角色(library)不在 registry;經 router 的統一解析補進清單。
        for manifest in CharacterLibrary().list_characters():
            character_id = str(manifest.get("id") or "")
            if character_id and character_id not in items:
                profile, _ = self._router.load_profile(character_id)
                items[character_id] = self._summarize(profile)
        return list(items.values())

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

    def add_playtime(self, character_id: str, seconds: int) -> None:
        """累加指定角色的遊玩秒數並更新最後遊玩時間戳記;供 UI 層週期性 flush 呼叫,
        取代直接開 SQLiteStore 寫入(見 week2-day2-uiux-layout-refinement 的封裝缺口)。"""
        profile, _ = self._router.load_profile(character_id)
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        if seconds > 0:
            total_seconds = int(store.get_setting(PLAYTIME_SECONDS_KEY, 0) or 0) + seconds
            store.set_setting(PLAYTIME_SECONDS_KEY, total_seconds)
        store.set_setting(LAST_PLAYED_AT_KEY, utc_now())

    def delete_character(self, character_id: str) -> dict[str, Any]:
        profile, is_library_character = self._router.load_profile(character_id)
        if profile.is_preset:
            raise ValueError(f"preset character cannot be deleted: {character_id}")
        gc.collect()
        if is_library_character:
            CharacterLibrary().delete_character(character_id)
            shutil.rmtree(Path("data") / "characters" / character_id, ignore_errors=True)
        else:
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
            "pending_offer": engine.store.get_setting("asset_pending_offer"),
            "pending_motion_offer": self._active_pending_motion_offer(engine.store),
        }

    def list_style_variants(self, character_id: str) -> list[dict[str, object]]:
        items = CharacterLibrary().list_variant_inventory(character_id)
        profile, _ = self._router.load_profile(character_id)
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        generating = {
            job.variant for job in AssetRepository(store).pending()
            if job.workflow_type == "variant_png" or job.workflow_type == "motion_clip" and job.motion_key == "idle"
        }
        motion_offer = self._active_pending_motion_offer(store)
        for item in items:
            # 未落地任何素材的格子不進「生成中」態,避免 HUD 顯示空白預覽的生成中格子。
            if item["variant"] in generating and item["state"] != "empty":
                item["state"] = "generating"
            elif motion_offer and item["variant"] == motion_offer["variant"]:
                item["state"] = "awaiting_confirm"
        return items

    def _active_pending_motion_offer(self, store: SQLiteStore) -> dict[str, Any] | None:
        offer = store.get_setting("asset_pending_motion_offer")
        if not offer:
            return None
        import config

        if self._offer_expired(offer.get("created_at"), config.PREVIEW_OFFER_TTL_HOURS):
            store.set_setting("asset_pending_motion_offer", None)
            return None
        return offer

    @staticmethod
    def _offer_expired(created_at: str | None, ttl_hours: float) -> bool:
        if not created_at:
            return False
        try:
            return datetime.now(UTC) - datetime.fromisoformat(created_at) > timedelta(hours=ttl_hours)
        except ValueError:
            return False

    def confirm_motion_generation(self, character_id: str, accept: bool) -> dict[str, Any]:
        profile, _ = self._router.load_profile(character_id)
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        offer = self._active_pending_motion_offer(store)
        if not offer:
            return {"accepted": False, "pending": False}
        if not accept:
            store.set_setting("asset_pending_motion_offer", None)
            store.set_setting("asset_generation_freeze", None)
            return {"accepted": False, "pending": False}
        response = build_asset_service(store, character_id, CharacterLibrary()).create_variant_motion_request(
            character_id, str(offer["variant"]), str(offer["source_png"]), str(offer["job_id"]), str(offer.get("reason", "")),
        )
        accepted = response.status in {"queued", "completed"} and response.metadata.get("service") != "mock_asset_service"
        if accepted:
            store.set_setting("asset_pending_motion_offer", None)
            store.set_setting("asset_generation_freeze", None)
        return {"accepted": accepted, "pending": bool(store.get_setting("asset_pending_motion_offer")), "asset": response.to_dict()}

    def apply_style(self, character_id: str, variant: str) -> dict[str, object]:
        library = CharacterLibrary()
        item = next((item for item in self.list_style_variants(character_id) if item["variant"] == variant), None)
        if item is None or item["state"] != "ready":
            raise ValueError(f"style is not ready: {variant}")
        manifest = library.set_active_variant(character_id, variant)
        if library.get_background_mode(character_id) == "follow":
            manifest = library.set_background(character_id, library.variant_background_path(character_id, variant))
        return {"character_id": character_id, "variant": variant, "background_image": manifest.get("background_image", "")}

    def list_scene_backgrounds(self, character_id: str) -> list[dict[str, object]]:
        return CharacterLibrary().list_background_scenes(character_id)

    def apply_scene(self, character_id: str, scene_id: str) -> dict[str, object]:
        library = CharacterLibrary()
        if scene_id == "follow":
            library.set_background_mode(character_id, "follow")
            active_variant = str((library.get_character(character_id) or {}).get("active_variant") or "og")
            manifest = library.set_background(character_id, library.variant_background_path(character_id, active_variant))
            return {"character_id": character_id, "background_mode": "follow", "background_image": manifest.get("background_image", "")}
        path = library.variant_background_path(character_id, scene_id)
        if not path:
            raise ValueError(f"scene background not ready: {scene_id}")
        library.set_background_mode(character_id, "manual")
        manifest = library.set_background(character_id, path)
        return {"character_id": character_id, "background_mode": "manual", "background_image": manifest.get("background_image", "")}

    def confirm_growth_offer(self, character_id: str, accept: bool) -> dict[str, Any]:
        profile, _ = self._router.load_profile(character_id)
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        offer = store.get_setting("asset_pending_offer")
        if not offer:
            return {"accepted": False, "pending": False}
        if not accept:
            store.set_setting("asset_pending_offer", None)
            return {"accepted": False, "pending": False}
        context = "\n".join(str(row["text"]) for row in self._active_memory_rows(store, character_id))[:2000]
        metadata = {"variant_type": str(offer["variant"]), "trigger_reason": str(offer["reason"])}
        if metadata["variant_type"] == "event":
            metadata["event_prompt"] = self._select_festival_prompt(store)
        response = build_asset_service(store, character_id, CharacterLibrary()).create_asset(AssetRequest(
            asset_type="variant_png", prompt_params={"generation_context": context},
            source_event_id=str(offer["source_event_id"]),
            metadata=metadata,
        ))
        accepted = response.status in {"queued", "completed"} and response.metadata.get("service") != "mock_asset_service"
        if accepted:
            store.set_setting("asset_pending_offer", None)
            store.set_setting("asset_generation_freeze", {"created_at": utc_now()})
        return {"accepted": accepted, "pending": bool(store.get_setting("asset_pending_offer")), "asset": response.to_dict()}

    @staticmethod
    def _select_festival_prompt(store: SQLiteStore) -> str:
        """三種節慶 prompt 排除前兩輪已用;僅在使用者確認 event offer 時消耗輪替順位。"""
        import config

        history = store.get_setting(FESTIVAL_PROMPT_HISTORY_KEY, []) or []
        candidates = [prompt for prompt in config.FESTIVAL_EVENT_PROMPTS if prompt not in history] or list(config.FESTIVAL_EVENT_PROMPTS)
        prompt = random.choice(candidates)
        store.set_setting(FESTIVAL_PROMPT_HISTORY_KEY, (list(history) + [prompt])[-2:])
        return prompt

    @staticmethod
    def _active_memory_rows(store: SQLiteStore, character_id: str):
        now = utc_now()
        with store.connect() as conn:
            return conn.execute("SELECT text FROM memory_items WHERE character_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC", (character_id, now)).fetchall()

    def trigger_skill(self, skill_id: str) -> dict[str, Any]:
        profile = self._router.get_active_character()
        engine = self._router.get_active_engine()
        if profile is None or engine is None:
            raise NoActiveCharacterError("no active character to trigger a skill for")
        # engine.skills 即 active character 的 resolved skill 集合
        # (profile skill_config + personal skill_refs + local skills)。
        skill = next((item for item in engine.skills if item.name == skill_id), None)
        if skill is None:
            raise ValueError(f"skill not available for active character: {skill_id}")
        if not skill.triggers:
            raise ValueError(f"skill has no trigger phrase configured: {skill_id}")

        event = self._router.dispatch_event({"text": skill.triggers[0], "source": "character_ui"})
        payload = event.to_dict()
        payload["user_text"] = f"立即執行：{skill.display_name or skill.name}"
        return payload

    def create_from_upload(self, image_path: str, name: str) -> dict[str, Any]:
        """上傳圖片走角色審核流程(character-validation-flow);回傳 job 資訊供前端輪詢。"""
        if not image_path or not Path(image_path).is_file():
            raise ValueError("請先選擇要上傳的圖片")
        if not (name or "").strip():
            raise ValueError("請輸入角色名稱")
        store = self._asset_store()
        service = build_asset_service(store, None, CharacterLibrary())
        response = service.create_character_validation_request(image_path, name.strip(), f"ui-{time.time_ns()}")
        return response.to_dict()

    def get_validation_status(self, job_id: str) -> dict[str, Any]:
        job = AssetRepository(self._asset_store()).get(job_id)
        if job is None:
            raise ValueError(f"validation job not found: {job_id}")
        # 過審 ≠ 可用:idle WebM 與背景都落地後,新角色才算就緒(character-validation-flow)。
        assets_ready = False
        if job.status.value == "completed" and job.character_id:
            library = CharacterLibrary()
            assets_ready = bool(
                library.get_motion_path(job.character_id, "idle")
                and library.get_background_path(job.character_id)
            )
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "character_id": job.character_id,
            "error_message": job.error_message,
            "assets_ready": assets_ready,
        }

    @staticmethod
    def _asset_store() -> SQLiteStore:
        # 與 ui/settings_dialog.py 共用同一顆全域 job 資料庫。
        store = SQLiteStore(Path("data") / "pet_state.db")
        store.initialize()
        return store

    def get_customization(self, character_id: str) -> dict[str, Any]:
        return self._customization.get_customization(character_id)

    def save_persona(self, character_id: str, persona: str | None) -> dict[str, Any]:
        return self._customization.save_persona(character_id, persona)

    def upsert_local_skill(self, character_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._customization.upsert_local_skill(character_id, payload)

    def delete_local_skill(self, character_id: str, skill_id: str) -> dict[str, Any]:
        return self._customization.delete_local_skill(character_id, skill_id)

    def save_skill_override(
        self, character_id: str, skill_id: str, aliases: list[str] | None, priority: int
    ) -> dict[str, Any]:
        return self._customization.save_skill_override(character_id, skill_id, aliases, priority)

    def preview_skill_match(self, character_id: str, text: str) -> dict[str, Any]:
        return self._customization.preview_skill_match(character_id, text)

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
