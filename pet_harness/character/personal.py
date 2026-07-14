"""Character personal:使用者可編輯的角色個人化資料契約(純資料,不是 plug-in)。

`data/characters/<character_id>/personal.json` 只允許 persona 文字、skill 參照與
per-skill alias/priority 覆寫:
- `skill_refs` 只能指向內建 `.agentic/skills/<id>.md`
- `local_skill_refs` 只能指向 `data/characters/<character_id>/skills/<id>.md`
- `skill_overrides` 只能對已授權的 skill_id 附加別名與非負 priority,不改變其
  canonical name/description/behavior/xp_reward/required_tool。
禁止可執行內容、secret、URL、絕對路徑、路徑跳脫與跨角色資源;任何違規
整份拒絕(不部分啟用),角色回退到 profile.json 的預設 persona/skill_config。

Schema v1(舊格式,無 skill_overrides)可直接載入,只有下一次成功的
customization 儲存才會落盤為 schema v2;v1 載入時 skill_overrides 一律為空。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from pet_harness.models.skill import Skill

LOGGER = logging.getLogger(__name__)

SCHEMA_V1 = 1
SCHEMA_V2 = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_V1, SCHEMA_V2})
MAX_PERSONA_LENGTH = 4000
MAX_DISPLAY_NAME_LENGTH = 120
MAX_SKILL_REFS = 32
MAX_ALIASES_PER_SKILL = 16
MAX_ALIAS_LENGTH = 64
MAX_PRIORITY = 1000

_SKILL_REF_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_ID_PATTERN = _SKILL_REF_PATTERN
_ALLOWED_KEYS_V1 = {"schema_version", "display_name", "persona", "skill_refs", "local_skill_refs"}
_ALLOWED_KEYS_V2 = _ALLOWED_KEYS_V1 | {"skill_overrides"}
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
    """personal.json、skill_overrides 或 local skill 驗證失敗;整份資料不得部分啟用。"""


@dataclass(frozen=True)
class SkillOverride:
    """單一 skill_id 的角色專屬覆寫:只有 aliases 與 priority,不含 canonical metadata。"""

    aliases: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class CharacterPersonal:
    schema_version: int
    display_name: str | None
    persona: str | None
    skill_refs: tuple[str, ...]
    local_skill_refs: tuple[str, ...]
    skill_overrides: dict[str, SkillOverride] = field(default_factory=dict)

    def to_document(self) -> dict[str, object]:
        """序列化為 schema v2 的 JSON-safe dict,供 write_personal 落盤前重新驗證用。"""
        return {
            "schema_version": SCHEMA_V2,
            "display_name": self.display_name,
            "persona": self.persona,
            "skill_refs": list(self.skill_refs),
            "local_skill_refs": list(self.local_skill_refs),
            "skill_overrides": {
                skill_id: {"aliases": list(override.aliases), "priority": override.priority}
                for skill_id, override in self.skill_overrides.items()
            },
        }


def load_personal(character_id: str, character_data_dir: Path) -> CharacterPersonal | None:
    """載入並驗證 personal.json;檔案不存在回傳 None,非法內容拋 PersonalValidationError。"""
    path = Path(character_data_dir) / "personal.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersonalValidationError(f"personal.json unreadable: {exc}") from exc
    return validate_document(payload)


def validate_document(payload: object) -> CharacterPersonal:
    """驗證一份候選 personal 文件(v1 或 v2)並回傳 CharacterPersonal;任何違規整份拒絕。"""
    if not isinstance(payload, dict):
        raise PersonalValidationError("personal document must be a JSON object")

    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise PersonalValidationError(f"unsupported personal schema_version: {schema_version!r}")

    allowed_keys = _ALLOWED_KEYS_V2 if schema_version == SCHEMA_V2 else _ALLOWED_KEYS_V1
    unknown = set(payload) - allowed_keys
    if unknown:
        # 未知欄位一律拒絕:可執行欄位(python/js/shell)、secret key 等都在此擋下。
        raise PersonalValidationError(f"personal document has forbidden keys: {sorted(unknown)}")

    display_name = _validate_text(payload.get("display_name"), "display_name", MAX_DISPLAY_NAME_LENGTH)
    persona = _validate_text(payload.get("persona"), "persona", MAX_PERSONA_LENGTH)
    skill_refs = _validate_refs(payload.get("skill_refs"), "skill_refs")
    local_skill_refs = _validate_refs(payload.get("local_skill_refs"), "local_skill_refs")
    skill_overrides = (
        _validate_overrides(payload.get("skill_overrides")) if schema_version == SCHEMA_V2 else {}
    )

    return CharacterPersonal(
        schema_version=schema_version,
        display_name=display_name,
        persona=persona,
        skill_refs=skill_refs,
        local_skill_refs=local_skill_refs,
        skill_overrides=skill_overrides,
    )


def write_personal(character_data_dir: Path, document: CharacterPersonal) -> None:
    """驗證候選文件後,原子寫入這個角色自己的 personal.json(暫存檔 + os.replace)。"""
    payload = document.to_document()
    validate_document(payload)  # 落盤前重新走一次驗證,保證讀寫規則一致
    path = Path(character_data_dir) / "personal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


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


def build_local_skill_document(skill_id: str, payload: dict[str, object]) -> tuple[str, Skill]:
    """驗證 create/update 用的 local skill payload,回傳 (markdown 內容, Skill)。不落地任何檔案。"""
    if not _SAFE_ID_PATTERN.fullmatch(str(skill_id)):
        raise PersonalValidationError(f"invalid local skill id: {skill_id!r}")

    name = str(payload.get("name") or skill_id).strip()
    if name != skill_id:
        raise PersonalValidationError(f"local skill name must match its skill_id: {skill_id!r}")
    display_name = str(payload.get("display_name") or "").strip()
    description = str(payload.get("description") or "").strip()
    behavior = str(payload.get("behavior") or "").strip()
    required_tool = str(payload.get("required_tool") or "").strip()
    unlock_reward = str(payload.get("unlock_reward") or "").strip()
    xp_reward = payload.get("xp_reward", 0)
    xp_reward_text = str(int(xp_reward)) if isinstance(xp_reward, (int, float)) and not isinstance(xp_reward, bool) else str(xp_reward).strip()

    triggers_value = payload.get("triggers", payload.get("trigger", ""))
    if isinstance(triggers_value, list):
        triggers_text = ", ".join(str(item).strip() for item in triggers_value if str(item).strip())
    else:
        triggers_text = str(triggers_value or "").strip()

    for field_name, value in (
        ("name", name),
        ("display_name", display_name),
        ("description", description),
        ("trigger", triggers_text),
        ("behavior", behavior),
        ("required_tool", required_tool),
        ("unlock_reward", unlock_reward),
    ):
        if value and _FORBIDDEN_VALUE_PATTERN.search(value):
            raise PersonalValidationError(f"local skill {skill_id} field {field_name} contains forbidden content")

    metadata = {
        "name": name,
        "display_name": display_name,
        "description": description,
        "trigger": triggers_text,
        "behavior": behavior,
        "xp_reward": xp_reward_text,
        "required_tool": required_tool,
        "unlock_reward": unlock_reward,
    }
    try:
        skill = Skill.from_metadata(metadata, file_path=None)
    except ValueError as exc:
        raise PersonalValidationError(f"local skill {skill_id} invalid: {exc}") from exc

    if skill.name != skill_id:
        raise PersonalValidationError(f"local skill name {skill.name!r} must match its skill_id {skill_id!r}")
    if not _SAFE_ID_PATTERN.fullmatch(skill.behavior):
        # behavior 是動作 key,不是路徑;禁止跨角色 motion path。
        raise PersonalValidationError(f"local skill {skill_id} behavior must be a plain motion key")
    if skill.required_tool and not _SAFE_ID_PATTERN.fullmatch(skill.required_tool):
        raise PersonalValidationError(f"local skill {skill_id} required_tool must be a plain tool id")

    lines = [f"name: {name}", f"description: {description}"]
    if display_name:
        lines.append(f"display_name: {display_name}")
    lines.append(f"trigger: {triggers_text}")
    lines.append(f"behavior: {behavior}")
    lines.append(f"xp_reward: {xp_reward_text}")
    if required_tool:
        lines.append(f"required_tool: {required_tool}")
    if unlock_reward:
        lines.append(f"unlock_reward: {unlock_reward}")
    markdown = "\n".join(lines) + "\n"
    return markdown, skill


def write_local_skill(character_data_dir: Path, skill_id: str, payload: dict[str, object]) -> Skill:
    """驗證後原子寫入 `<character_data_dir>/skills/<skill_id>.md`,回傳附上 file_path 的 Skill。"""
    markdown, skill = build_local_skill_document(skill_id, payload)
    skills_dir = (Path(character_data_dir) / "skills").resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = (skills_dir / f"{skill_id}.md").resolve()
    if path.parent != skills_dir:
        raise PersonalValidationError(f"local skill id escapes character skills dir: {skill_id!r}")
    tmp_path = path.with_suffix(".md.tmp")
    tmp_path.write_text(markdown, encoding="utf-8")
    os.replace(tmp_path, path)
    return replace(skill, file_path=str(path))


def delete_local_skill_file(character_data_dir: Path, skill_id: str) -> None:
    """刪除這個角色自己的 local skill 檔案;不存在時視為已刪除(no-op)。"""
    if not _SKILL_REF_PATTERN.fullmatch(str(skill_id)):
        raise PersonalValidationError(f"invalid local skill ref: {skill_id!r}")
    skills_dir = (Path(character_data_dir) / "skills").resolve()
    path = (skills_dir / f"{skill_id}.md").resolve()
    if path.parent != skills_dir:
        raise PersonalValidationError(f"local skill ref escapes character skills dir: {skill_id!r}")
    if path.exists():
        path.unlink()


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


def _validate_overrides(value) -> dict[str, SkillOverride]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PersonalValidationError("skill_overrides must be an object")
    if len(value) > MAX_SKILL_REFS:
        raise PersonalValidationError(f"skill_overrides exceeds {MAX_SKILL_REFS} entries")
    overrides: dict[str, SkillOverride] = {}
    for skill_id, entry in value.items():
        if not isinstance(skill_id, str) or not _SKILL_REF_PATTERN.fullmatch(skill_id):
            raise PersonalValidationError(f"invalid skill_overrides key: {skill_id!r}")
        if not isinstance(entry, dict):
            raise PersonalValidationError(f"skill_overrides[{skill_id}] must be an object")
        unknown = set(entry) - {"aliases", "priority"}
        if unknown:
            raise PersonalValidationError(f"skill_overrides[{skill_id}] has forbidden keys: {sorted(unknown)}")
        aliases = _validate_aliases(entry.get("aliases"), skill_id)
        priority = entry.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0 or priority > MAX_PRIORITY:
            raise PersonalValidationError(f"skill_overrides[{skill_id}].priority must be an int in [0, {MAX_PRIORITY}]")
        overrides[skill_id] = SkillOverride(aliases=aliases, priority=priority)
    return overrides


def _validate_aliases(value, skill_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PersonalValidationError(f"skill_overrides[{skill_id}].aliases must be a list")
    if len(value) > MAX_ALIASES_PER_SKILL:
        raise PersonalValidationError(f"skill_overrides[{skill_id}].aliases exceeds {MAX_ALIASES_PER_SKILL} entries")
    aliases: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PersonalValidationError(f"skill_overrides[{skill_id}].aliases entry invalid: {item!r}")
        normalized = item.strip()
        if len(normalized) > MAX_ALIAS_LENGTH:
            raise PersonalValidationError(f"skill_overrides[{skill_id}].aliases entry exceeds {MAX_ALIAS_LENGTH} characters")
        if _FORBIDDEN_VALUE_PATTERN.search(normalized):
            raise PersonalValidationError(f"skill_overrides[{skill_id}].aliases entry contains forbidden content")
        if normalized not in aliases:
            aliases.append(normalized)
    return tuple(aliases)
