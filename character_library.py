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


PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_WEBM_DIR = PROJECT_ROOT / "assets" / "webm"
CHARACTER_LIBRARY_DIR = ASSETS_WEBM_DIR / "characters"
UI_ASSETS_DIR = PROJECT_ROOT / "ui" / "assets"
UI_BACKGROUNDS_DIR = UI_ASSETS_DIR / "backgrounds"
UI_MUSIC_DIR = UI_ASSETS_DIR / "music"

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
    """管理角色來源圖與輸出動作;只依傳入的 character_id 解析 manifest,
    不保存 authoritative active character(唯一權威是 CharacterRouter snapshot)。"""

    def __init__(self):
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

    def get_idle_motion_candidates(self, character_id: str | None) -> list[dict[str, object]]:
        manifest = self.get_character(character_id)
        if not manifest:
            return []

        candidates: dict[str, dict[str, object]] = {}
        normalized_character_id = str(character_id or manifest.get("id") or "").strip()
        base_idle_path = self.get_motion_path(normalized_character_id, "idle") if normalized_character_id else None
        if base_idle_path:
            candidates[base_idle_path] = {"path": base_idle_path, "weight": 4}

        idle_pool = manifest.get("idle_pool")
        if not isinstance(idle_pool, list):
            return list(candidates.values())

        for entry in idle_pool:
            resolved = self._resolve_idle_pool_entry(manifest, entry, base_idle_path)
            if resolved is None:
                continue
            candidate_path, weight = resolved
            candidates[candidate_path] = {"path": candidate_path, "weight": weight}
        return list(candidates.values())

    def get_action_motion_path(self, character_id: str, action_key: str) -> str | None:
        if action_key not in ACTION_MOTION_KEYS:
            return None
        return self.get_motion_path(character_id, action_key)

    def list_action_tags(self, character_id: str | None) -> list[str]:
        """列出目前角色 manifest 明確宣告且可解析的 AI action tags。"""
        manifest = self.get_character(character_id)
        if not manifest:
            return []
        actions = manifest.get("actions")
        if not isinstance(actions, dict):
            return []
        return [
            tag for tag in actions
            if isinstance(tag, str) and self.resolve_action_tag(str(character_id), tag) is not None
        ]

    def resolve_action_tag(self, character_id: str | None, action_tag: str | None) -> dict[str, str] | None:
        """將 action tag 限定解析到指定角色的 manifest motion。

        不接受其他角色、demo mapping 或任意路徑。找不到 tag 或檔案時回傳 None，
        由呼叫端保留同角色 idle fallback。
        """
        normalized_character_id = str(character_id or "").strip()
        normalized_tag = str(action_tag or "").strip()
        if not normalized_character_id or not normalized_tag:
            return None
        manifest = self.get_character(normalized_character_id)
        if not manifest:
            return None
        actions = manifest.get("actions")
        if not isinstance(actions, dict):
            return None
        motion_key = actions.get(normalized_tag)
        if not isinstance(motion_key, str) or not motion_key.strip():
            return None
        motion_key = motion_key.strip()
        motions = manifest.get("motions")
        if not isinstance(motions, dict) or motion_key not in motions:
            return None
        path = self.get_motion_path(normalized_character_id, motion_key)
        if not path:
            return None
        return {"action_tag": normalized_tag, "motion_key": motion_key, "path": path}

    def is_valid_action_tag(self, character_id: str | None, action_tag: str | None) -> bool:
        return self.resolve_action_tag(character_id, action_tag) is not None

    def _resolve_idle_pool_entry(
        self,
        manifest: dict,
        entry: object,
        base_idle_path: str | None,
    ) -> tuple[str, int] | None:
        candidate = ""
        weight: int | None = None
        if isinstance(entry, str):
            candidate = entry.strip()
        elif isinstance(entry, dict):
            candidate = str(
                entry.get("motion")
                or entry.get("path")
                or entry.get("relative_path")
                or entry.get("filename")
                or ""
            ).strip()
            raw_weight = entry.get("weight")
            if raw_weight is not None:
                try:
                    weight = int(raw_weight)
                except (TypeError, ValueError):
                    weight = None
        else:
            return None

        resolved_path = self._resolve_idle_candidate_path(manifest, candidate, base_idle_path)
        if not resolved_path:
            return None
        if weight is None:
            weight = 4 if base_idle_path and Path(resolved_path) == Path(base_idle_path) else 2
        return resolved_path, max(1, int(weight))

    def _resolve_idle_candidate_path(
        self,
        manifest: dict,
        candidate: str,
        base_idle_path: str | None,
    ) -> str | None:
        normalized = str(candidate or "").strip()
        if not normalized:
            return None
        if normalized == "idle":
            return base_idle_path if base_idle_path and Path(base_idle_path).is_file() else None

        motions = manifest.get("motions") if isinstance(manifest.get("motions"), dict) else {}
        motion_candidate = motions.get(normalized)
        if motion_candidate:
            resolved = self._resolve_manifest_asset_path(motion_candidate)
            if resolved:
                return resolved

        resolved = self._resolve_manifest_asset_path(normalized)
        if resolved:
            return resolved

        motions_dir = manifest.get("motions_dir")
        if isinstance(motions_dir, str) and motions_dir.strip():
            return self._resolve_manifest_asset_path(str(Path(motions_dir) / normalized))
        return None

    @staticmethod
    def _resolve_manifest_asset_path(candidate: str | None) -> str | None:
        normalized = str(candidate or "").strip()
        if not normalized:
            return None
        path = Path(normalized)
        absolute_path = path if path.is_absolute() else PROJECT_ROOT / normalized
        if absolute_path.suffix.lower() != ".webm":
            return None
        if not absolute_path.is_file():
            return None
        return str(absolute_path)

    _PANEL_MOTION_FILENAMES: dict[str, str] = {
        "report_news": "News_Panel.webm",
        "play_music": "Play_Music_Panel.webm",
    }

    def get_panel_motion_path(self, character_id: str, action_key: str) -> str | None:
        manifest = self.get_character(character_id)
        if not manifest:
            return None
        filename = manifest.get("panel_motions", {}).get(action_key) or self._PANEL_MOTION_FILENAMES.get(action_key)
        if not filename:
            return None
        motions_dir = manifest.get("motions_dir")
        if not motions_dir:
            return None
        absolute_path = PROJECT_ROOT / motions_dir / filename
        if not absolute_path.is_file():
            return None
        return str(absolute_path)

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

    def get_layout_config(self, character_id: str | None) -> dict:
        manifest = self.get_character(character_id)
        if not manifest:
            return {}
        layout = manifest.get("layout")
        return dict(layout) if isinstance(layout, dict) else {}

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
