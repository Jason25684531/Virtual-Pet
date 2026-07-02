from pathlib import Path
import json
import shutil

from pet_harness.character.profile import CharacterProfile
from pet_harness.character.exceptions import (
    CharacterAlreadyExistsError,
    CharacterNotFoundError,
)


class CharacterRegistry:
    """多角色生命週期管理：雙目錄 CRUD 與 active_character 切換。

    - assets_dir: manifest.json / motions 所在（assets/webm/characters/）
    - data_dir: profile.json / state.db 所在（data/characters/）
    """

    def __init__(
        self,
        assets_dir: str = "assets/webm/characters",
        data_dir: str = "data/characters",
    ):
        self._assets_dir = Path(assets_dir)
        self._data_dir = Path(data_dir)
        self._active: CharacterProfile | None = None

    # ------------------------------------------------------------------
    # 內部路徑輔助
    # ------------------------------------------------------------------

    def _manifest_path(self, character_id: str) -> Path:
        return self._assets_dir / character_id / "manifest.json"

    def _profile_path(self, character_id: str) -> Path:
        return self._data_dir / character_id / "profile.json"

    def _exists(self, character_id: str) -> bool:
        return (
            self._manifest_path(character_id).exists()
            and self._profile_path(character_id).exists()
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_character(
        self,
        character_id: str,
        name: str,
        persona_description: str,
        skill_config: list[str],
        voice_id_env_key: str,
        layout: dict | None = None,
    ) -> CharacterProfile:
        """建立角色雙目錄骨架並寫入初始 manifest.json / profile.json。"""
        layout = layout if layout is not None else {}
        motions_dir = (self._assets_dir / character_id / "motions").as_posix()

        # 先建構 CharacterProfile 觸發 character_id 格式驗證，
        # 非法 id 在落盤前就會拋 InvalidCharacterIdError。
        profile = CharacterProfile(
            character_id=character_id,
            name=name,
            background_image="",
            motions_dir=motions_dir,
            motions={},
            idle_pool=[],
            voice_id_env_key=voice_id_env_key,
            layout=layout,
            persona_description=persona_description,
            skill_config=skill_config,
        )

        assets_char_dir = self._assets_dir / character_id
        data_char_dir = self._data_dir / character_id
        if assets_char_dir.exists() or data_char_dir.exists():
            raise CharacterAlreadyExistsError(
                f"character '{character_id}' already exists"
            )

        (assets_char_dir / "motions").mkdir(parents=True)
        data_char_dir.mkdir(parents=True)

        manifest = {
            "id": character_id,
            "name": name,
            "background_image": "",
            "motions_dir": motions_dir,
            "motions": {},
            "idle_pool": [],
            "voice_id_env_key": voice_id_env_key,
            "layout": layout,
        }
        self._manifest_path(character_id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        profile_data = {
            "persona_description": persona_description,
            "skill_config": skill_config,
        }
        self._profile_path(character_id).write_text(
            json.dumps(profile_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return profile

    def load_character(self, character_id: str) -> CharacterProfile:
        """載入角色；manifest.json 與 profile.json 缺一即視為不存在。"""
        if not self._exists(character_id):
            raise CharacterNotFoundError(f"character '{character_id}' not found")
        return CharacterProfile.load(character_id)

    def list_characters(self) -> list[CharacterProfile]:
        """掃描 assets_dir，只回傳雙檔案齊全且可成功載入的角色。"""
        characters: list[CharacterProfile] = []
        if not self._assets_dir.exists():
            return characters
        for entry in sorted(self._assets_dir.iterdir()):
            if not entry.is_dir():
                continue
            character_id = entry.name
            if not self._exists(character_id):
                continue
            try:
                characters.append(CharacterProfile.load(character_id))
            except Exception as exc:  # 優雅降級：壞檔跳過，不讓列表崩潰
                print(
                    f"[CharacterRegistry] Warning: failed to load "
                    f"'{character_id}': {exc}"
                )
        return characters

    def update_profile(
        self,
        character_id: str,
        persona_description: str | None = None,
        skill_config: list[str] | None = None,
    ) -> CharacterProfile:
        """更新 profile.json 中的給定欄位，未給定的欄位維持原值。"""
        profile = self.load_character(character_id)
        if persona_description is not None:
            profile.persona_description = persona_description
        if skill_config is not None:
            profile.skill_config = skill_config
        profile.save()
        return profile

    def update_manifest(self, character_id: str, manifest_patch: dict) -> None:
        """manifest.json 唯一寫入口，保留給 AssetManager 使用。

        淺層 merge：patch 內的 key 整份覆蓋原值。不觸碰 profile.json。
        """
        manifest_path = self._manifest_path(character_id)
        if not manifest_path.exists():
            raise CharacterNotFoundError(f"character '{character_id}' not found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(manifest_patch)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def delete_character(self, character_id: str) -> None:
        """移除角色的 assets 與 data 雙目錄；若為 active 角色則清除 active。"""
        assets_char_dir = self._assets_dir / character_id
        data_char_dir = self._data_dir / character_id
        if not assets_char_dir.exists() and not data_char_dir.exists():
            raise CharacterNotFoundError(f"character '{character_id}' not found")
        shutil.rmtree(assets_char_dir, ignore_errors=True)
        shutil.rmtree(data_char_dir, ignore_errors=True)
        if self._active is not None and self._active.character_id == character_id:
            self._active = None

    # ------------------------------------------------------------------
    # active_character 狀態
    # ------------------------------------------------------------------

    def set_active(self, character_id: str) -> None:
        """切換 active 角色；不存在時拋 CharacterNotFoundError。"""
        self._active = self.load_character(character_id)

    def get_active(self) -> CharacterProfile | None:
        """回傳目前 active 角色，未設定時為 None。"""
        return self._active
