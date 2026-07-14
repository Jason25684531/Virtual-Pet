"""任務 4.4:personal/local skill 契約測試 — 合法資料只影響本角色,非法資料整份拒絕。"""

import json
from pathlib import Path

import pytest

import pet_harness.character.profile as profile_module
from pet_harness.character.personal import PersonalValidationError, load_local_skills, load_personal
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.runtime.provider_runtime import ProviderRuntime
from tests.conftest import FakeProvider

_SKILL_FIXTURES = {
    "joke_skill": {"trigger": "joke, funny", "behavior": "laugh", "xp_reward": "5"},
    "music_skill": {"trigger": "music, bgm", "behavior": "play_music", "xp_reward": "4"},
    "gacha_skill": {"trigger": "gacha, luck", "behavior": "idle", "xp_reward": "2"},
}


def _write_skill(skills_dir: Path, name: str, meta: dict[str, str]) -> None:
    lines = [
        f"name: {name}",
        f"description: fixture skill {name}",
        f"trigger: {meta['trigger']}",
        f"behavior: {meta['behavior']}",
        f"xp_reward: {meta['xp_reward']}",
    ]
    (skills_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_character(root: Path, character_id: str, skill_config: list[str]) -> None:
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id,
        "name": character_id,
        "background_image": "",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": {},
        "idle_pool": [],
        "voice_id_env_key": "",
        "layout": {},
    }
    (assets_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    data_dir = root / "data" / "characters" / character_id
    data_dir.mkdir(parents=True)
    (data_dir / "profile.json").write_text(
        json.dumps({"persona_description": f"{character_id} default persona", "skill_config": skill_config}),
        encoding="utf-8",
    )


def _write_personal(root: Path, character_id: str, payload: dict) -> Path:
    path = root / "data" / "characters" / character_id / "personal.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_local_skill(root: Path, character_id: str, name: str, extra_lines: list[str] | None = None) -> None:
    skills_dir = root / "data" / "characters" / character_id / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"name: {name}",
        f"description: local skill {name}",
        "trigger: cheer, dance",
        "behavior: laugh",
        "xp_reward: 1",
    ] + (extra_lines or [])
    (skills_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    _write_character(tmp_path, "Choppr", ["joke_skill"])
    _write_character(tmp_path, "miku", ["music_skill"])
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    skills_dir = tmp_path / ".agentic" / "skills"
    skills_dir.mkdir(parents=True)
    for name, meta in _SKILL_FIXTURES.items():
        _write_skill(skills_dir, name, meta)

    registry = CharacterRegistry(
        assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
        data_dir=str(tmp_path / "data" / "characters"),
    )
    router = CharacterRouter(
        registry=registry,
        agentic_root=str(tmp_path / ".agentic"),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )
    return tmp_path, router


def test_valid_personal_customizes_only_its_character(env):
    tmp_path, router = env
    _write_local_skill(tmp_path, "miku", "local_cheer")
    _write_personal(
        tmp_path,
        "miku",
        {
            "schema_version": 1,
            "persona": "custom miku persona",
            "skill_refs": ["gacha_skill"],
            "local_skill_refs": ["local_cheer"],
        },
    )

    router.switch_character("miku")
    miku_engine = router.get_active_engine()
    miku_profile = router.get_active_character()
    assert miku_profile.effective_persona == "custom miku persona"
    assert {s.name for s in miku_engine.skills} == {"music_skill", "gacha_skill", "local_cheer"}

    router.switch_character("Choppr")
    choppr_engine = router.get_active_engine()
    choppr_profile = router.get_active_character()
    # miku 的 personal/local skill 不影響 Choppr;切換後 local skill 不再 routable
    assert choppr_profile.effective_persona == "Choppr default persona"
    assert {s.name for s in choppr_engine.skills} == {"joke_skill"}
    assert "local_cheer" not in set(router.get_active_snapshot().skill_refs)


def test_personal_with_executable_field_is_rejected_entirely(env):
    tmp_path, router = env
    _write_personal(
        tmp_path,
        "miku",
        {
            "schema_version": 1,
            "persona": "valid persona text",
            "javascript": "require('child_process').exec('calc')",
        },
    )

    router.switch_character("miku")
    profile = router.get_active_character()
    # 整份拒絕:即使 persona 欄位本身合法也不得部分啟用
    assert profile.personal is None
    assert profile.effective_persona == "miku default persona"


def test_personal_with_traversal_skill_ref_is_rejected(env):
    tmp_path, router = env
    _write_personal(
        tmp_path,
        "miku",
        {"schema_version": 1, "local_skill_refs": ["../Choppr"]},
    )

    router.switch_character("miku")
    assert router.get_active_character().personal is None


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "persona": "see https://evil.example/prompt"},
        {"schema_version": 1, "persona": "api_key = sk-12345"},
        {"schema_version": 1, "persona": "x" * 5000},
        {"schema_version": 3, "persona": "unsupported future schema"},
        {"schema_version": 1, "skill_refs": ["/abs/path"]},
        {"schema_version": 1, "skill_refs": ["good", "..\\bad"]},
        {"schema_version": 2, "skill_overrides": {"music_skill": {"priority": -1}}},
        {"schema_version": 2, "skill_overrides": {"music_skill": {"priority": 5, "unknown_key": True}}},
        {"schema_version": 2, "skill_overrides": {"../bad": {"aliases": ["x"]}}},
        {"schema_version": 2, "skill_overrides": {"music_skill": {"aliases": ["see https://evil.example"]}}},
        {"schema_version": 1, "skill_overrides": {"music_skill": {"priority": 1}}},
    ],
)
def test_unsafe_personal_payloads_raise(tmp_path, payload):
    char_dir = tmp_path / "miku"
    char_dir.mkdir()
    (char_dir / "personal.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PersonalValidationError):
        load_personal("miku", char_dir)


def test_local_skill_with_url_content_rejects_whole_batch(tmp_path):
    skills_dir = tmp_path / "data" / "characters" / "miku" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "bad_skill.md").write_text(
        "name: bad_skill\ndescription: fetch https://evil.example\ntrigger: x\nbehavior: laugh\nxp_reward: 1\n",
        encoding="utf-8",
    )
    _write_local_skill(tmp_path, "miku", "good_skill")

    character_data_dir = tmp_path / "data" / "characters" / "miku"
    with pytest.raises(PersonalValidationError):
        load_local_skills("miku", ["good_skill", "bad_skill"], character_data_dir)


def test_local_skill_behavior_must_be_plain_motion_key(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "sneaky.md").write_text(
        "name: sneaky\ndescription: cross character\ntrigger: x\nbehavior: ../miku/motions/idle\nxp_reward: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(PersonalValidationError):
        load_local_skills("Choppr", ["sneaky"], tmp_path)


def test_missing_personal_file_returns_none(tmp_path):
    assert load_personal("miku", tmp_path) is None


def test_v1_document_loads_with_empty_overrides(tmp_path):
    char_dir = tmp_path / "miku"
    char_dir.mkdir()
    (char_dir / "personal.json").write_text(
        json.dumps({"schema_version": 1, "persona": "v1 persona", "skill_refs": ["music_skill"]}),
        encoding="utf-8",
    )

    document = load_personal("miku", char_dir)

    assert document.schema_version == 1
    assert document.persona == "v1 persona"
    assert document.skill_overrides == {}


def test_v2_document_round_trips_with_overrides(tmp_path):
    from pet_harness.character.personal import SCHEMA_V2, SkillOverride, CharacterPersonal, write_personal

    char_dir = tmp_path / "miku"
    char_dir.mkdir(parents=True)
    document = CharacterPersonal(
        schema_version=SCHEMA_V2,
        display_name=None,
        persona="v2 persona",
        skill_refs=("music_skill",),
        local_skill_refs=(),
        skill_overrides={"music_skill": SkillOverride(aliases=("放歌",), priority=5)},
    )

    write_personal(char_dir, document)
    reloaded = load_personal("miku", char_dir)

    assert reloaded.schema_version == 2
    assert reloaded.persona == "v2 persona"
    assert reloaded.skill_overrides["music_skill"].aliases == ("放歌",)
    assert reloaded.skill_overrides["music_skill"].priority == 5


def test_write_personal_never_touches_another_character_dir(tmp_path):
    from pet_harness.character.personal import SCHEMA_V2, CharacterPersonal, write_personal

    miku_dir = tmp_path / "miku"
    choppr_dir = tmp_path / "Choppr"
    miku_dir.mkdir(parents=True)
    choppr_dir.mkdir(parents=True)
    (choppr_dir / "personal.json").write_text(
        json.dumps({"schema_version": 1, "persona": "choppr persona"}), encoding="utf-8"
    )

    document = CharacterPersonal(
        schema_version=SCHEMA_V2, display_name=None, persona="miku persona",
        skill_refs=(), local_skill_refs=(), skill_overrides={},
    )
    write_personal(miku_dir, document)

    assert json.loads((choppr_dir / "personal.json").read_text(encoding="utf-8"))["persona"] == "choppr persona"
    assert (miku_dir / "personal.json").exists()
    assert not (miku_dir / "personal.json.tmp").exists()


def test_build_local_skill_document_rejects_unsafe_behavior_and_forbidden_content():
    from pet_harness.character.personal import PersonalValidationError, build_local_skill_document

    with pytest.raises(PersonalValidationError):
        build_local_skill_document(
            "sneaky",
            {"description": "d", "trigger": "x", "behavior": "../escape", "xp_reward": 1},
        )
    with pytest.raises(PersonalValidationError):
        build_local_skill_document(
            "leaky",
            {"description": "see https://evil.example", "trigger": "x", "behavior": "laugh", "xp_reward": 1},
        )


def test_write_local_skill_is_atomic_and_scoped_to_character_dir(tmp_path):
    from pet_harness.character.personal import delete_local_skill_file, write_local_skill

    char_dir = tmp_path / "miku"
    char_dir.mkdir(parents=True)
    skill = write_local_skill(
        char_dir, "local_cheer",
        {"description": "cheer up", "triggers": ["加油", "cheer"], "behavior": "laugh", "xp_reward": 1},
    )

    assert skill.name == "local_cheer"
    assert (char_dir / "skills" / "local_cheer.md").exists()
    assert not (char_dir / "skills" / "local_cheer.md.tmp").exists()

    delete_local_skill_file(char_dir, "local_cheer")
    assert not (char_dir / "skills" / "local_cheer.md").exists()
