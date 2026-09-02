"""
ECHOES — 角色資產庫
管理角色資料夾、manifest、動作檔案與目前套用中的角色。
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from pet_harness.storage.sqlite_store import SQLiteStore


PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_WEBM_DIR = PROJECT_ROOT / "assets" / "webm"
CHARACTER_LIBRARY_DIR = PROJECT_ROOT / "assets" / "characters"
LEGACY_CHARACTER_LIBRARY_DIR = ASSETS_WEBM_DIR / "characters"
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
#情緒對應詞
CANONICAL_ACTION_ALIASES = {"annoy": "angry", "waving": "wave_response"}
LEGACY_TO_CANONICAL_ACTION_ALIASES = {legacy: canonical for canonical, legacy in CANONICAL_ACTION_ALIASES.items()}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-_")
    return cleaned or "character"


def _character_id(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", value).strip("-").lower()
    return slug or f"char-{secrets.token_hex(4)}"


class CharacterLibrary:
    """管理角色來源圖與輸出動作;只依傳入的 character_id 解析 manifest,
    不保存 authoritative active character(唯一權威是 CharacterRouter snapshot)。"""

    def __init__(self):
        CHARACTER_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        UI_MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    def list_characters(self) -> list[dict]:
        manifests = []
        for manifest_path in [*CHARACTER_LIBRARY_DIR.glob("*/manifest.json"), *LEGACY_CHARACTER_LIBRARY_DIR.glob("*/manifest.json")]:
            try:
                manifests.append(self._load_manifest(manifest_path))
            except (OSError, json.JSONDecodeError):
                continue
        manifests.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return list({item["id"]: item for item in manifests}.values())

    def get_character(self, character_id: str | None) -> dict | None:
        if not character_id:
            return None

        manifest_path = self._manifest_path(character_id)
        if not manifest_path.is_file():
            return None
        return self._load_manifest(manifest_path)

    def get_voice_gender(self, character_id: str | None) -> str:
        manifest = self.get_character(character_id)
        return str(manifest.get("voice_gender") or "") if manifest else ""

    def delete_character(self, character_id: str) -> None:
        character_dir = CHARACTER_LIBRARY_DIR / character_id
        if not character_dir.is_dir():
            raise FileNotFoundError(f"character not found: {character_id}")
        shutil.rmtree(character_dir)

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
            "active_variant": "og",
            "selected_generations": {},
            "positive_prompt": "",
            "negative_prompt": "",
        }
        self._save_manifest(character_id, manifest)
        return manifest

    def create_validated_character(self, character_id: str, image_path: str, display_name: str, voice_gender: str = "") -> dict:
        source_path = Path(image_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"找不到角色圖片: {image_path}")
        character_id = character_id or _character_id(display_name)
        character_dir = CHARACTER_LIBRARY_DIR / character_id
        if character_dir.exists():
            raise FileExistsError(f"角色已存在: {character_id}")
        og_dir = character_dir / "images" / "og"
        motions_dir = character_dir / "motions"
        og_dir.mkdir(parents=True)
        motions_dir.mkdir()
        og_path = og_dir / f"{character_id}{source_path.suffix.lower() or '.png'}"
        shutil.copy2(source_path, og_path)
        manifest = {
            "id": character_id,
            "name": display_name.strip() or character_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "source_image": self._to_relative(og_path),
            "source_dir": self._to_relative(og_dir),
            "motions_dir": self._to_relative(motions_dir),
            "motions": {},
            "active_variant": "og",
            "selected_generations": {},
            "background_image": "",
            "background_mode": "follow",
            "voice_gender": voice_gender,
            "positive_prompt": "",
            "negative_prompt": "",
        }
        self._save_manifest(character_id, manifest)
        return manifest

    def get_background_mode(self, character_id: str) -> str:
        manifest = self.get_character(character_id)
        return "manual" if manifest and manifest.get("background_mode") == "manual" else "follow"

    def set_background_mode(self, character_id: str, mode: str) -> dict:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"找不到角色資料: {character_id}")
        manifest["background_mode"] = "manual" if mode == "manual" else "follow"
        manifest["updated_at"] = _now_iso()
        self._save_manifest(character_id, manifest)
        return manifest

    def list_background_scenes(self, character_id: str) -> list[dict[str, object]]:
        manifest = self.get_character(character_id)
        if not manifest:
            return []
        background_root = self._manifest_path(character_id).parent / "images" / "bg"
        if not background_root.is_dir():
            return []
        current = manifest.get("background_image") or ""
        return [
            {"scene_id": path.stem, "thumb": self._to_relative(path), "is_current": self._to_relative(path) == current}
            for path in sorted(background_root.glob("*.png"))
        ]

    def set_background(self, character_id: str, image_path: str) -> dict:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"找不到角色資料: {character_id}")
        manifest["background_image"] = self._to_relative(Path(image_path)) if image_path else ""
        manifest["updated_at"] = _now_iso()
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

    def set_active_variant(self, character_id: str, variant: str) -> dict:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"character not found: {character_id}")
        manifest["active_variant"] = str(variant or "og").strip() or "og"
        manifest["updated_at"] = _now_iso()
        self._save_manifest(character_id, manifest)
        return manifest

    def get_motion_path(self, character_id: str, motion_key: str) -> str | None:
        manifest = self.get_character(character_id)
        if not manifest:
            return None
        variant = str(manifest.get("active_variant") or "og")
        generation = self._selected_wearable_generation(character_id, variant)
        if generation is not None:
            candidate = self._generation_motion_path(character_id, variant, generation, motion_key)
            if candidate:
                return candidate
        motions_dir = manifest.get("motions_dir")
        relative_path = manifest.get("motions", {}).get(motion_key)
        if relative_path:
            absolute_path = PROJECT_ROOT / relative_path
            if absolute_path.is_file():
                return str(absolute_path)
        if generation is None and motions_dir:
            candidate = PROJECT_ROOT / str(motions_dir) / f"{motion_key}.webm"
            if candidate.is_file():
                return str(candidate)
        # A missing key never searches another revision. OG is the only
        # cross-variant fallback allowed by the contract.
        og_generation = self._selected_wearable_generation(character_id, "og")
        if variant != "og" and og_generation is not None:
            candidate = self._generation_motion_path(character_id, "og", og_generation, motion_key)
            if candidate:
                return candidate
        if variant != "og":
            og_flat = self._manifest_path(character_id).parent / "motions" / "og" / f"{motion_key}.webm"
            if og_flat.is_file():
                return str(og_flat)
        return None

    def list_variant_inventory(self, character_id: str) -> list[dict[str, object]]:
        manifest = self.get_character(character_id)
        if not manifest:
            return []
        root = self._manifest_path(character_id).parent
        images_root, motions_root = root / "images", root / "motions"
        variants = set()
        if images_root.is_dir():
            variants.update(path.name for path in images_root.iterdir() if path.is_dir() and path.name != "bg")
        if motions_root.is_dir():
            variants.update(path.name for path in motions_root.iterdir() if path.is_dir())
        background_root = images_root / "bg"
        if background_root.is_dir():
            variants.update(path.stem for path in background_root.glob("*.png"))
        active_variant = str(manifest.get("active_variant") or "og")
        items = []
        for variant in sorted(variants):
            self._ensure_flat_import(character_id, variant)
            revisions = self._revision_inventory(character_id, variant)
            wearable = [item for item in revisions if item["wearable"]]
            images = sorted((images_root / variant).glob("*.png"), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
            item = {
                "variant": variant,
                "state": "ready" if wearable else "generating" if images else "empty",
                "thumb": self._to_relative(images[0]) if images else "",
                "is_active": variant == active_variant,
            }
            if revisions:
                selected = self._selected_wearable_generation(character_id, variant)
                item.update({"revisions": wearable, "selected_generation": selected, "revision_count": len(wearable)})
            items.append(item)
        return items

    def variant_background_path(self, character_id: str, variant: str) -> str | None:
        generation = self._selected_wearable_generation(character_id, variant)
        if generation is not None:
            path = self._generation_background_path(character_id, variant, generation)
            if path:
                return path
        if generation is None:
            rows = self._asset_store(character_id).list_character_assets(
                character_id, active_only=False, variant=variant, asset_type="character_background_png"
            )
            if rows:
                path = Path(str(rows[-1]["file_path"]))
                if not path.is_absolute():
                    path = PROJECT_ROOT / path
                if path.is_file():
                    return str(path)
        path = self._manifest_path(character_id).parent / "images" / "bg" / f"{variant}.png"
        return str(path) if path.is_file() else None

    def select_style_generation(self, character_id: str, variant: str, asset_id: str) -> dict:
        manifest = self.get_character(character_id)
        if not manifest:
            raise FileNotFoundError(f"character not found: {character_id}")
        revisions = [item for item in self._revision_inventory(character_id, variant) if item["wearable"]]
        selected = next((item for item in revisions if item.get("asset_id") == asset_id), None)
        if selected is None:
            raise ValueError(f"style generation is not wearable: {asset_id}")
        pointers = manifest.setdefault("selected_generations", {})
        pointers[variant] = {"asset_id": selected["asset_id"], "generation": selected["generation"]}
        manifest["updated_at"] = _now_iso()
        self._save_manifest(character_id, manifest)
        return {"character_id": character_id, "variant": variant, "selected_generation": selected["generation"], "asset_id": selected["asset_id"]}

    def auto_select_wearable_generation(self, character_id: str, variant: str) -> dict | None:
        revisions = [item for item in self._revision_inventory(character_id, variant) if item["wearable"]]
        if not revisions:
            return None
        manifest = self.get_character(character_id)
        pointer = (manifest or {}).get("selected_generations", {}).get(variant)
        current = next((item for item in revisions if pointer and item["generation"] == pointer.get("generation") and item["asset_id"] == pointer.get("asset_id")), None)
        selected = current or revisions[-1]
        if current is None:
            self.select_style_generation(character_id, variant, str(selected["asset_id"]))
        return selected

    def _asset_store(self, character_id: str) -> SQLiteStore:
        primary = PROJECT_ROOT / "data" / "characters" / character_id / "state.db"
        fallback = PROJECT_ROOT / "state.db"
        store = SQLiteStore(primary if primary.exists() or not fallback.exists() else fallback)
        store.initialize()
        return store

    def _ensure_flat_import(self, character_id: str, variant: str) -> None:
        self._asset_store(character_id).ensure_flat_revision_registered(
            character_id, variant, self._manifest_path(character_id).parent
        )

    def _revision_inventory(self, character_id: str, variant: str) -> list[dict[str, object]]:
        self._ensure_flat_import(character_id, variant)
        rows = self._asset_store(character_id).list_character_assets(character_id, active_only=False, variant=variant)
        grouped: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            if row.get("generation_index") is None:
                continue
            grouped.setdefault(int(row["generation_index"]), []).append(row)
        result = []
        for generation, assets in sorted(grouped.items()):
            idle = next((row for row in assets if row.get("asset_type") == "motion_webm" and row.get("motion_key") == "idle"), None)
            idle_path = Path(str(idle["file_path"])) if idle else self._generation_motion_file(character_id, variant, generation, "idle")
            if not idle_path.is_absolute():
                idle_path = PROJECT_ROOT / idle_path
            wearable = idle_path.is_file()
            png = next((row for row in assets if row.get("asset_type") == "character_variant_png"), None)
            primary = png or idle or assets[0]
            result.append({
                "asset_id": str(primary["asset_id"]), "generation": generation,
                "wearable": wearable, "file_path": str(primary["file_path"]),
            })
        return result

    def _selected_wearable_generation(self, character_id: str, variant: str) -> int | None:
        revisions = [item for item in self._revision_inventory(character_id, variant) if item["wearable"]]
        if not revisions:
            return None
        manifest = self.get_character(character_id) or {}
        pointer = (manifest.get("selected_generations") or {}).get(variant) or {}
        selected = next((item for item in revisions if item["generation"] == pointer.get("generation") and item["asset_id"] == pointer.get("asset_id")), None)
        if selected:
            return int(selected["generation"])
        return int(revisions[-1]["generation"])

    def _has_revision_history(self, character_id: str, variant: str) -> bool:
        return bool(self._asset_store(character_id).list_character_assets(character_id, active_only=False, variant=variant))

    def _generation_motion_file(self, character_id: str, variant: str, generation: int, motion_key: str) -> Path:
        root = self._manifest_path(character_id).parent / "motions" / variant
        revision = root / f"g{generation:02d}" / f"{motion_key}.webm"
        if revision.is_file() or generation != 1:
            return revision
        return root / f"{motion_key}.webm"

    def _generation_motion_path(self, character_id: str, variant: str, generation: int, motion_key: str) -> str | None:
        path = self._generation_motion_file(character_id, variant, generation, motion_key)
        return str(path) if path.is_file() else None

    def _generation_background_path(self, character_id: str, variant: str, generation: int) -> str | None:
        root = self._manifest_path(character_id).parent / "images" / "bg"
        revision = root / f"{variant}-g{generation:02d}.png"
        path = revision if revision.is_file() or generation != 1 else root / f"{variant}.png"
        return str(path) if path.is_file() else None

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
        actions = self._manifest_actions(manifest)
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
        if not normalized_character_id or not normalized_tag or normalized_tag.lower() == "idle":
            return None
        normalized_tag = LEGACY_TO_CANONICAL_ACTION_ALIASES.get(normalized_tag, normalized_tag)
        manifest = self.get_character(normalized_character_id)
        if not manifest:
            return None
        actions = self._manifest_actions(manifest)
        motion_key = actions.get(normalized_tag)
        if not isinstance(motion_key, str) or not motion_key.strip() or motion_key.strip().lower() == "idle":
            return None
        motion_key = motion_key.strip()
        motions = manifest.get("motions")
        if not isinstance(motions, dict) or motion_key not in motions:
            return None
        path = self.get_motion_path(normalized_character_id, motion_key)
        if not path:
            return None
        return {"action_tag": normalized_tag, "motion_key": motion_key, "path": path}

    @staticmethod
    def _manifest_actions(manifest: dict) -> dict:
        actions = manifest.get("actions")
        if not isinstance(actions, dict):
            motions = manifest.get("motions")
            if not isinstance(motions, dict):
                return {}
            actions = {key: key for key in motions if isinstance(key, str) and key.lower() != "idle"}
        effective = {}
        for key, motion_key in actions.items():
            if not isinstance(key, str):
                continue
            canonical = LEGACY_TO_CANONICAL_ACTION_ALIASES.get(key, key)
            if canonical != key and canonical in actions:
                continue
            effective[canonical] = motion_key
        return effective

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
        if manifest.get("background_mode", "follow") != "manual":
            path = self.variant_background_path(character_id, str(manifest.get("active_variant") or "og"))
            if path:
                return path
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
        primary = CHARACTER_LIBRARY_DIR / character_id / "manifest.json"
        legacy = LEGACY_CHARACTER_LIBRARY_DIR / character_id / "manifest.json"
        return primary if primary.exists() or not legacy.exists() else legacy

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
