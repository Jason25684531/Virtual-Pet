"""Character personal:使用者可編輯的角色個人化資料契約(純資料,不是 plug-in)。

`data/characters/<character_id>/personal.json` 只允許 persona 文字與 skill 參照:
- `skill_refs` 只能指向內建 `.agentic/skills/<id>.md`
- `local_skill_refs` 只能指向 `data/characters/<character_id>/skills/<id>.md`
禁止可執行內容、secret、URL、絕對路徑、路徑跳脫與跨角色資源;任何違規
整份拒絕(不部分啟用),角色回退到 profile.json 的預設 persona/skill_config。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pet_harness.models.skill import Skill

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_PERSONA_LENGTH = 4000
MAX_DISPLAY_NAME_LENGTH = 120
MAX_SKILL_REFS = 32

_SKILL_REF_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_ID_PATTERN = _SKILL_REF_PATTERN
_ALLOWED_KEYS = {"schema_version", "display_name", "persona", "skill_refs", "local_skill_refs"}
# 任何值出現這些樣式都拒絕:URL、協定、secret-looking 內容。
_FORBIDDEN_VALUE_PATTERN = re.compile(
    r"://|(?:api[_-]?key|secret|token|password)\s*[:=]",
    re.IGNORECASE,
)
_ALLOWED_LOCAL_SKILL_FIELDS = {
    "name", "display_name", "description", "trigger", "behavior",
    "xp_reward", "required_tool", "unlock_reward",
}


class PersonalValidationError(ValueError):
    """personal.json 或 local skill 驗證失敗;整份資料不得部分啟用。"""


@dataclass(frozen=True)
class CharacterPersonal:
    schema_version: int
    display_name: str | None
    persona: str | None
    skill_refs: tuple[str, ...]
    local_skill_refs: tuple[str, ...]


def load_personal(character_id: str, character_data_dir: Path) -> CharacterPersonal | None:
    """載入並驗證 personal.json;檔案不存在回傳 None,非法內容拋 PersonalValidationError。"""
    path = Path(character_data_dir) / "personal.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersonalValidationError(f"personal.json unreadable: {exc}") from exc

    if not isinstance(payload, dict):
        raise PersonalValidationError("personal.json must be a JSON object")

    unknown = set(payload) - _ALLOWED_KEYS
    if unknown:
        # 未知欄位一律拒絕:可執行欄位(python/js/shell)、secret key 等都在此擋下。
        raise PersonalValidationError(f"personal.json has forbidden keys: {sorted(unknown)}")

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise PersonalValidationError(
            f"unsupported personal schema_version: {payload.get('schema_version')!r}"
        )

    display_name = _validate_text(payload.get("display_name"), "display_name", MAX_DISPLAY_NAME_LENGTH)
    persona = _validate_text(payload.get("persona"), "persona", MAX_PERSONA_LENGTH)
    skill_refs = _validate_refs(payload.get("skill_refs"), "skill_refs")
    local_skill_refs = _validate_refs(payload.get("local_skill_refs"), "local_skill_refs")

    return CharacterPersonal(
        schema_version=SCHEMA_VERSION,
        display_name=display_name,
        persona=persona,
        skill_refs=skill_refs,
        local_skill_refs=local_skill_refs,
    )


def load_local_skills(
    character_id: str,
    local_skill_refs: tuple[str, ...] | list[str],
    character_data_dir: Path,
) -> list[Skill]:
    """metadata-only local skill loader,僅限 `<character_data_dir>/skills/`。

    任一 ref 非法即整批拒絕(拋 PersonalValidationError),避免部分啟用。
    """
    if not local_skill_refs:
        return []
    skills_dir = (Path(character_data_dir) / "skills").resolve()
    skills: list[Skill] = []
    for ref in local_skill_refs:
        if not _SKILL_REF_PATTERN.fullmatch(str(ref)):
            raise PersonalValidationError(f"invalid local skill ref: {ref!r}")
        path = (skills_dir / f"{ref}.md").resolve()
        if path.parent != skills_dir:
            raise PersonalValidationError(f"local skill ref escapes character skills dir: {ref!r}")
        if not path.is_file():
            raise PersonalValidationError(f"local skill file not found: {ref}")
        skills.append(_parse_local_skill(path, ref))
    return skills


def _parse_local_skill(path: Path, ref: str) -> Skill:
    metadata: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        normalized_key = key.strip().lower()
        if normalized_key not in _ALLOWED_LOCAL_SKILL_FIELDS:
            continue  # metadata-only:非白名單欄位一律忽略,無可執行內容
        normalized_value = value.strip()
        if _FORBIDDEN_VALUE_PATTERN.search(normalized_value):
            raise PersonalValidationError(f"local skill {ref} field {normalized_key} contains forbidden content")
        metadata[normalized_key] = normalized_value

    try:
        skill = Skill.from_metadata(metadata, file_path=path)
    except ValueError as exc:
        raise PersonalValidationError(f"local skill {ref} invalid: {exc}") from exc

    if skill.name != ref:
        raise PersonalValidationError(f"local skill name {skill.name!r} must match its file ref {ref!r}")
    if not _SAFE_ID_PATTERN.fullmatch(skill.behavior):
        # behavior 是動作 key,不是路徑;禁止跨角色 motion path。
        raise PersonalValidationError(f"local skill {ref} behavior must be a plain motion key")
    if skill.required_tool and not _SAFE_ID_PATTERN.fullmatch(skill.required_tool):
        raise PersonalValidationError(f"local skill {ref} required_tool must be a plain tool id")
    return skill


def _validate_text(value, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PersonalValidationError(f"{field_name} must be a string")
    if len(value) > max_length:
        raise PersonalValidationError(f"{field_name} exceeds {max_length} characters")
    if _FORBIDDEN_VALUE_PATTERN.search(value):
        raise PersonalValidationError(f"{field_name} contains forbidden content (URL or secret-like text)")
    return value.strip() or None


def _validate_refs(value, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PersonalValidationError(f"{field_name} must be a list of skill IDs")
    if len(value) > MAX_SKILL_REFS:
        raise PersonalValidationError(f"{field_name} exceeds {MAX_SKILL_REFS} entries")
    refs: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _SKILL_REF_PATTERN.fullmatch(item):
            # 只接受純 skill ID:絕對路徑、../、URL、副檔名都不符合此樣式。
            raise PersonalValidationError(f"{field_name} entry is not a valid skill ID: {item!r}")
        refs.append(item)
    return tuple(refs)
