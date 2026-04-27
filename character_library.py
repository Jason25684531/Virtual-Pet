"""
ECHOES — 角色資產庫
管理角色資料夾、manifest、動作檔案與目前套用中的角色。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QSettings


PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_WEBM_DIR = PROJECT_ROOT / "assets" / "webm"
CHARACTER_LIBRARY_DIR = ASSETS_WEBM_DIR / "characters"
UI_ASSETS_DIR = PROJECT_ROOT / "ui" / "assets"
UI_BACKGROUNDS_DIR = UI_ASSETS_DIR / "backgrounds"
UI_MUSIC_DIR = UI_ASSETS_DIR / "music"

_SETTINGS_ORG = "ECHOES"
_SETTINGS_APP = "VirtualPet"
_SETTINGS_CURRENT_CHARACTER = "current_character_id"

MOTION_SPECS = [
    {"key": "laugh", "title": "雀躍大笑", "filename": "laugh.webm", "play_once": True},
    {"key": "angry", "title": "薄怒嘟嘴", "filename": "angry.webm", "play_once": True},
    {"key": "awkward", "title": "尷尬擺手", "filename": "awkward.webm", "play_once": True},
    {"key": "speechless", "title": "無言微翻白眼", "filename": "speechless.webm", "play_once": True},
    {"key": "listen", "title": "專心聆聽", "filename": "listen.webm", "play_once": True},
    {"key": "idle", "title": "愉悅微笑", "filename": "idle.webm", "play_once": False},
]
ACTION_MOTION_SPECS = [
    {"key": "report_news", "title": "新聞播報", "filename": "report_news.webm", "play_once": True},
    {"key": "play_music", "title": "音樂播放", "filename": "play_music.webm", "play_once": True},
    {"key": "wave_response", "title": "揮手回應", "filename": "running_forward.webm", "play_once": True},
]
ACTION_MOTION_KEYS = {spec["key"] for spec in ACTION_MOTION_SPECS}
MOTION_MAP = {spec["key"]: spec for spec in [*MOTION_SPECS, *ACTION_MOTION_SPECS]}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-_")
    return cleaned or "character"


class CharacterLibrary:
    """管理角色來源圖、輸出動作與目前套用角色。"""

    def __init__(self):
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        CHARACTER_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        UI_MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    def list_characters(self) -> list[dict]:
        manifests = []
        for manifest_path in CHARACTER_LIBRARY_DIR.glob("*/manifest.json"):
            try:
                manifests.append(self._load_manifest(manifest_path))
            except (OSError, json.JSONDecodeError):
                continue
        manifests.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return manifests

    def get_character(self, character_id: str | None) -> dict | None:
        if not character_id:
            return None

        manifest_path = self._manifest_path(character_id)
        if not manifest_path.is_file():
            return None
        return self._load_manifest(manifest_path)

    def create_character(self, image_path: str, display_name: str = "") -> dict:
        source_path = Path(image_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"找不到角色圖片: {image_path}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = _slugify(display_name or source_path.stem)
        character_id = f"{timestamp}_{base_name}"

        character_dir = CHARACTER_LIBRARY_DIR / character_id
        source_dir = character_dir / "source"
        motions_dir = character_dir / "motions"
        source_dir.mkdir(parents=True, exist_ok=False)
        motions_dir.mkdir(parents=True, exist_ok=True)

        copied_name = f"source{source_path.suffix.lower()}"
        copied_path = source_dir / copied_name
        shutil.copy2(source_path, copied_path)

        manifest = {
            "id": character_id,
            "name": display_name.strip() or source_path.stem,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "source_image": self._to_relative(copied_path),
            "source_dir": self._to_relative(source_dir),
            "motions_dir": self._to_relative(motions_dir),
            "motions": {},
            "positive_prompt": "",
            "negative_prompt": "",
        }
        self._save_manifest(character_id, manifest)
        return manifest

    def register_generated_assets(
        self,
        character_id: str,
        archived_files: dict[str, str],
        positive_prompt: str = "",
        negative_prompt: str = "",
    ) -> dict:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"找不到角色資料: {character_id}")

        motions = manifest.setdefault("motions", {})
        for motion_key, file_path in archived_files.items():
            motions[motion_key] = self._to_relative(Path(file_path))

        manifest["updated_at"] = _now_iso()
        manifest["positive_prompt"] = positive_prompt
        manifest["negative_prompt"] = negative_prompt
        self._save_manifest(character_id, manifest)
        self.set_current_character_id(character_id)
        return manifest

    def get_motion_path(self, character_id: str, motion_key: str) -> str | None:
        manifest = self.get_character(character_id)
        if not manifest:
            return None

        relative_path = manifest.get("motions", {}).get(motion_key)
        if not relative_path:
            return None

        absolute_path = PROJECT_ROOT / relative_path
        if not absolute_path.is_file():
            return None
        return str(absolute_path)

    def get_action_motion_path(self, character_id: str, action_key: str) -> str | None:
        if action_key not in ACTION_MOTION_KEYS:
            return None
        return self.get_motion_path(character_id, action_key)

    def get_background_path(self, character_id: str) -> str | None:
        manifest = self.get_character(character_id)
        if not manifest:
            return None
        relative_path = manifest.get("background_image")
        if not relative_path:
            return None
        absolute_path = PROJECT_ROOT / relative_path
        if not absolute_path.is_file():
            return None
        return str(absolute_path)

    def get_character_name(self, character_id: str | None) -> str | None:
        manifest = self.get_character(character_id)
        if not manifest:
            return None
        return manifest.get("name") or manifest.get("id")

    def get_preview_image_path(self, character_id: str) -> str | None:
        manifest = self.get_character(character_id)
        if not manifest:
            return None

        relative_path = manifest.get("source_image")
        if not relative_path:
            return None

        absolute_path = PROJECT_ROOT / relative_path
        if not absolute_path.is_file():
            return None
        return str(absolute_path)

    def get_source_dir_path(self, character_id: str) -> str:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"找不到角色資料: {character_id}")
        return str(PROJECT_ROOT / manifest["source_dir"])

    def get_motions_dir_path(self, character_id: str) -> str:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"找不到角色資料: {character_id}")
        return str(PROJECT_ROOT / manifest["motions_dir"])

    def set_current_character_id(self, character_id: str):
        self._settings.setValue(_SETTINGS_CURRENT_CHARACTER, character_id)

    def get_current_character_id(self) -> str | None:
        value = self._settings.value(_SETTINGS_CURRENT_CHARACTER, "", type=str)
        return value or None

    def _manifest_path(self, character_id: str) -> Path:
        return CHARACTER_LIBRARY_DIR / character_id / "manifest.json"

    def _save_manifest(self, character_id: str, manifest: dict):
        manifest_path = self._manifest_path(character_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)

    def _load_manifest(self, manifest_path: Path) -> dict:
        with open(manifest_path, "r", encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _to_relative(path: Path) -> str:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
