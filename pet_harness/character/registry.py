from pathlib import Path
import shutil

from pet_harness.character.profile import CharacterProfile
from pet_harness.character.exceptions import (
    CharacterNotFoundError,
)


class CharacterRegistry:
    """多角色載入與刪除：雙目錄（assets + data）。

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

    def delete_character(self, character_id: str) -> None:
        """移除角色的 assets 與 data 雙目錄。"""
        assets_char_dir = self._assets_dir / character_id
        data_char_dir = self._data_dir / character_id
        if not assets_char_dir.exists() and not data_char_dir.exists():
            raise CharacterNotFoundError(f"character '{character_id}' not found")
        shutil.rmtree(assets_char_dir, ignore_errors=True)
        shutil.rmtree(data_char_dir, ignore_errors=True)
