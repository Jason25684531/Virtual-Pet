"""角色個人化(persona/local skill/內建 skill override)的驗證式讀寫服務。

每次寫入先驗證整份候選 personal 文件與 local skill metadata,再原子取代目標檔;
只有編輯的角色正好是 active character 時才觸發 runtime refresh(透過
CharacterRouter.switch_character 重新載入該角色的 profile + engine),
編輯非 active 角色時完全不動 runtime。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pet_harness.character import personal
from pet_harness.character import profile as profile_module
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.models.skill import Skill
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.storage.sqlite_store import SQLiteStore

_UNSET = object()


class CharacterCustomizationService:
    """角色 persona / local skill / 內建 skill 別名-priority 覆寫的唯一寫入口。"""

    def __init__(
        self,
        registry: CharacterRegistry,
        router: CharacterRouter,
        project_root: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        # 沿用 profile_module._PROJECT_ROOT（而非另建常數），讓測試對它的 monkeypatch 生效，
        # 避免預設值繞過測試隔離、寫進真實的 data/characters/。
        self._project_root = Path(project_root) if project_root is not None else profile_module._PROJECT_ROOT

    def get_customization(self, character_id: str) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        return self._build_view(profile)

    def save_persona(self, character_id: str, persona_text: str | None) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        previous_persona = profile.effective_persona
        candidate = self._mutate(profile, persona=persona_text)
        personal.write_personal(self._character_dir(character_id), candidate)
        # 人設真的變了才清歷史:舊身份的問答殘留在對話歷史/記憶裡,會在下一次
        # 互動時反過來蓋掉新人設(LLM 傾向跟隨歷史裡重複出現的答案)。
        new_persona = candidate.persona or profile.persona_description
        if new_persona != previous_persona:
            self._clear_character_history(character_id)
        self._refresh_if_active(character_id)
        return self.get_customization(character_id)

    def _clear_character_history(self, character_id: str) -> None:
        profile = self._registry.load_character(character_id)
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        store.clear_events()
        active = self._router.get_active_character()
        active_engine = self._router.get_active_engine()
        if active is not None and active.character_id == character_id and active_engine is not None:
            active_engine.memory_store.clear()

    def upsert_local_skill(self, character_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        skill_id = str(payload.get("skill_id") or payload.get("name") or "").strip()
        skill_payload = dict(payload)
        skill_payload["name"] = skill_id
        # 先驗證 skill metadata 本身(不落地),再驗證整份 personal 候選文件;
        # 兩者都合法才依序寫檔,避免半啟用。
        personal.build_local_skill_document(skill_id, skill_payload)
        existing_refs = list(profile.personal.local_skill_refs) if profile.personal else []
        if skill_id not in existing_refs:
            existing_refs = existing_refs + [skill_id]
        candidate = self._mutate(profile, local_skill_refs=existing_refs)
        personal.write_local_skill(self._character_dir(character_id), skill_id, skill_payload)
        personal.write_personal(self._character_dir(character_id), candidate)
        self._refresh_if_active(character_id)
        return self.get_customization(character_id)

    def delete_local_skill(self, character_id: str, skill_id: str) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        remaining_refs = [
            ref for ref in (profile.personal.local_skill_refs if profile.personal else ())
            if ref != skill_id
        ]
        candidate = self._mutate(profile, local_skill_refs=remaining_refs)
        personal.write_personal(self._character_dir(character_id), candidate)
        personal.delete_local_skill_file(self._character_dir(character_id), skill_id)
        self._refresh_if_active(character_id)
        return self.get_customization(character_id)

    def save_skill_override(
        self,
        character_id: str,
        skill_id: str,
        aliases: list[str] | None,
        priority: int,
    ) -> dict[str, Any]:
        profile = self._registry.load_character(character_id)
        builtin_names = {skill.name for skill in self._load_builtin_skills()}
        if skill_id not in set(profile.allowed_skill_refs) or skill_id not in builtin_names:
            raise personal.PersonalValidationError(f"skill not authorized for override: {skill_id}")
        candidate = self._mutate(profile, override_patch=(skill_id, aliases, priority))
        personal.write_personal(self._character_dir(character_id), candidate)
        self._refresh_if_active(character_id)
        return self.get_customization(character_id)

    def preview_skill_match(self, character_id: str, text: str) -> dict[str, Any]:
        """非執行預覽:必須是目前 active 角色,直接複用其 runtime router 保證與正式路由一致。"""
        active_profile = self._router.get_active_character()
        if active_profile is None or active_profile.character_id != character_id:
            raise ValueError("skill match preview requires the active character")
        engine = self._router.get_active_engine()
        diagnostics = dict(engine.preview_skill_match(text))
        local_names = set(active_profile.personal.local_skill_refs) if active_profile.personal else set()

        def _source(skill_id: str) -> str:
            return "local" if skill_id in local_names else "builtin"

        diagnostics["source"] = _source(diagnostics["skill_id"]) if diagnostics.get("matched") else None
        diagnostics["candidates"] = [
            {**candidate, "source": _source(candidate["skill_id"])}
            for candidate in diagnostics.get("candidates", [])
        ]
        return diagnostics

    # ------------------------------------------------------------------
    # 內部輔助
    # ------------------------------------------------------------------

    def _character_dir(self, character_id: str) -> Path:
        return self._project_root / "data" / "characters" / character_id

    def _load_builtin_skills(self) -> list[Skill]:
        return SkillLoader(self._project_root / ".agentic" / "skills").load_skills()

    def _mutate(
        self,
        profile: CharacterProfile,
        *,
        persona: str | None = _UNSET,
        local_skill_refs: list[str] = _UNSET,
        override_patch: tuple[str, list[str] | None, int] = _UNSET,
    ) -> personal.CharacterPersonal:
        current = profile.personal
        payload: dict[str, Any] = dict(current.to_document()) if current is not None else {
            "schema_version": personal.SCHEMA_V2,
            "display_name": None,
            "persona": None,
            "skill_refs": [],
            "local_skill_refs": [],
            "skill_overrides": {},
        }
        payload["schema_version"] = personal.SCHEMA_V2
        if persona is not _UNSET:
            payload["persona"] = persona
        if local_skill_refs is not _UNSET:
            payload["local_skill_refs"] = list(local_skill_refs)
        if override_patch is not _UNSET:
            skill_id, aliases, priority = override_patch
            overrides = dict(payload.get("skill_overrides") or {})
            overrides[skill_id] = {"aliases": list(aliases or []), "priority": priority}
            payload["skill_overrides"] = overrides
        return personal.validate_document(payload)

    def _refresh_if_active(self, character_id: str) -> None:
        active = self._router.get_active_character()
        if active is not None and active.character_id == character_id:
            self._router.switch_character(character_id)

    def _build_view(self, profile: CharacterProfile) -> dict[str, Any]:
        builtin_skills = self._load_builtin_skills()
        overrides = profile.personal.skill_overrides if profile.personal else {}
        authorized_ids = set(profile.allowed_skill_refs)
        local_skills = profile.load_local_skills()
        return {
            "character_id": profile.character_id,
            "persona": profile.effective_persona,
            "schema_version": profile.personal.schema_version if profile.personal else personal.SCHEMA_V1,
            "builtin_skills": [
                {
                    "skill_id": skill.name,
                    "display_name": skill.display_name or skill.name,
                    "description": skill.description,
                    "canonical_triggers": list(skill.triggers),
                    "behavior": skill.behavior,
                    "required_tool": skill.required_tool,
                    "aliases": list(overrides[skill.name].aliases) if skill.name in overrides else [],
                    "priority": overrides[skill.name].priority if skill.name in overrides else 0,
                }
                for skill in builtin_skills
                if skill.name in authorized_ids
            ],
            "local_skills": [
                {
                    "skill_id": skill.name,
                    "display_name": skill.display_name or skill.name,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "behavior": skill.behavior,
                    "required_tool": skill.required_tool,
                }
                for skill in local_skills
            ],
        }
