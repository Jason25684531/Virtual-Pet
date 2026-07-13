from dataclasses import dataclass, field
from pathlib import Path
import json
import logging
import re

from pet_harness.character.exceptions import InvalidCharacterIdError
from pet_harness.character.personal import (
    CharacterPersonal,
    PersonalValidationError,
    load_local_skills,
    load_personal,
)

_LOGGER = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class CharacterProfile:
    """合併 manifest.json + profile.json 的角色資料層。"""

    # 來自 manifest.json
    character_id: str
    name: str
    background_image: str
    motions_dir: str
    motions: dict[str, str]
    idle_pool: list[dict]
    voice_id_env_key: str
    layout: dict

    # 來自 profile.json
    persona_description: str
    skill_config: list[str]

    # 來自 manifest.json（可選，預設 False 以向後相容舊 manifest）
    is_preset: bool = False

    # 來自 personal.json(可選;驗證失敗時為 None,角色以 profile.json 預設運作)
    personal: CharacterPersonal | None = None

    # 自動計算
    sqlite_path: str = field(init=False)
    qdrant_collection: str = field(init=False)

    def __post_init__(self) -> None:
        if not _ID_PATTERN.match(self.character_id):
            raise InvalidCharacterIdError(
                f"character_id '{self.character_id}' must match [a-zA-Z0-9_]+"
            )
        self.sqlite_path = f"data/characters/{self.character_id}/state.db"
        self.qdrant_collection = f"{self.character_id}_memory"

    @classmethod
    def load(cls, character_id: str) -> "CharacterProfile":
        """從 manifest.json + profile.json 合併載入。"""
        manifest_path = (
            _PROJECT_ROOT
            / "assets"
            / "webm"
            / "characters"
            / character_id
            / "manifest.json"
        )
        profile_path = (
            _PROJECT_ROOT / "data" / "characters" / character_id / "profile.json"
        )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = json.loads(profile_path.read_text(encoding="utf-8"))

        character_data_dir = _PROJECT_ROOT / "data" / "characters" / character_id
        try:
            personal = load_personal(character_id, character_data_dir)
            if personal is not None and personal.local_skill_refs:
                # 先驗證 local skills;任一非法即整份 personal 拒絕,不部分啟用。
                load_local_skills(character_id, personal.local_skill_refs, character_data_dir)
        except PersonalValidationError as exc:
            _LOGGER.warning("personal.json rejected for %s: %s", character_id, exc)
            personal = None

        return cls(
            character_id=manifest["id"],
            name=manifest["name"],
            background_image=manifest["background_image"],
            motions_dir=manifest["motions_dir"],
            motions=manifest["motions"],
            idle_pool=manifest["idle_pool"],
            voice_id_env_key=manifest.get("voice_id_env_key", ""),
            layout=manifest["layout"],
            persona_description=profile["persona_description"],
            skill_config=profile["skill_config"],
            is_preset=bool(manifest.get("is_preset", False)),
            personal=personal,
        )

    @property
    def effective_persona(self) -> str:
        """personal.persona 優先;否則相容讀取舊 profile.json 的 persona_description。"""
        if self.personal is not None and self.personal.persona:
            return self.personal.persona
        return self.persona_description

    @property
    def allowed_skill_refs(self) -> list[str]:
        """profile.json skill_config + personal 宣告的內建 skill_refs(去重、保序)。"""
        refs = list(self.skill_config)
        if self.personal is not None:
            refs.extend(ref for ref in self.personal.skill_refs if ref not in refs)
        return refs

    def load_local_skills(self) -> list:
        """載入此角色 personal 宣告的 character-local metadata-only skills。"""
        if self.personal is None or not self.personal.local_skill_refs:
            return []
        character_data_dir = _PROJECT_ROOT / "data" / "characters" / self.character_id
        try:
            return load_local_skills(self.character_id, self.personal.local_skill_refs, character_data_dir)
        except PersonalValidationError as exc:
            _LOGGER.warning("local skills rejected for %s: %s", self.character_id, exc)
            return []

    def save(self) -> None:
        """只寫 profile.json，永不修改 manifest.json。"""
        profile_path = (
            _PROJECT_ROOT / "data" / "characters" / self.character_id / "profile.json"
        )
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "persona_description": self.persona_description,
            "skill_config": self.skill_config,
        }
        profile_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def to_json(self) -> str:
        """序列化為 JSON 字串，包含所有欄位。"""
        data = {
            "character_id": self.character_id,
            "name": self.name,
            "background_image": self.background_image,
            "motions_dir": self.motions_dir,
            "motions": self.motions,
            "idle_pool": self.idle_pool,
            "voice_id_env_key": self.voice_id_env_key,
            "layout": self.layout,
            "persona_description": self.persona_description,
            "skill_config": self.skill_config,
            "is_preset": self.is_preset,
            "sqlite_path": self.sqlite_path,
            "qdrant_collection": self.qdrant_collection,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "CharacterProfile":
        """從 JSON 字串重建 CharacterProfile。"""
        data = json.loads(json_str)
        return cls(
            character_id=data["character_id"],
            name=data["name"],
            background_image=data["background_image"],
            motions_dir=data["motions_dir"],
            motions=data["motions"],
            idle_pool=data["idle_pool"],
            voice_id_env_key=data["voice_id_env_key"],
            layout=data["layout"],
            persona_description=data["persona_description"],
            skill_config=data["skill_config"],
            is_preset=bool(data.get("is_preset", False)),
        )
