"""fixed-character-roster:六個固定預設角色、固定順序、資產健康狀態與舊存檔相容。"""

from __future__ import annotations

import json

import pytest

import config
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.ui.character_ui_service import CharacterUiService

pytestmark = pytest.mark.uses_repo_cwd

EXPECTED_ROSTER = ("char-Adol", "char-Jack", "char-Kai", "char-Luke", "char-Nico", "char-ROG")
# char-Omni 的資料夾是大寫,但 manifest 宣告的權威 ID 是 char-omni —— 舊存檔就是用這個 ID 存的。
LEGACY_CHARACTER_IDS = ("char-omni", "char-Zenni")


def _service() -> CharacterUiService:
    registry = CharacterRegistry()
    return CharacterUiService(router=CharacterRouter(registry=registry), registry=registry)


def test_default_roster_is_declared_once_in_config():
    assert config.DEFAULT_CHARACTER_IDS == EXPECTED_ROSTER


def test_roster_order_is_fixed_and_independent_of_asset_update_time():
    listed = [item["character_id"] for item in _service().list_characters()]
    assert listed[: len(EXPECTED_ROSTER)] == list(EXPECTED_ROSTER)


def test_roster_order_is_stable_across_repeated_listings():
    service = _service()
    assert [item["character_id"] for item in service.list_characters()] == [
        item["character_id"] for item in service.list_characters()
    ]


def test_non_roster_characters_are_kept_after_the_fixed_block():
    listed = [item["character_id"] for item in _service().list_characters()]
    # 固定版面不得刪除其他角色(舊存檔仍要看得到並可載入)
    assert set(listed) >= {*EXPECTED_ROSTER, *LEGACY_CHARACTER_IDS}
    assert all(listed.index(cid) < listed.index("char-omni") for cid in EXPECTED_ROSTER)


@pytest.mark.parametrize("character_id", EXPECTED_ROSTER)
def test_every_roster_character_reports_its_asset_health(character_id):
    """2.2:缺資產時保留卡片並回報原因;目前六個角色的必要資產應齊全。"""
    item = {entry["character_id"]: entry for entry in _service().list_characters()}[character_id]
    assert item["missing_assets"] == [], f"{character_id} 缺少資產: {item['missing_assets']}"
    assert item["asset_status"] == "ok"


@pytest.mark.parametrize("character_id", EXPECTED_ROSTER)
def test_card_preview_stage_and_voice_all_resolve_from_one_profile(character_id):
    """2.3:卡片、預覽背景、舞台動作與聲線必須來自同一份 profile,不各自解析。"""
    from character_library import CharacterLibrary

    profile, _ = CharacterRouter(registry=CharacterRegistry()).load_profile(character_id)
    card = {entry["character_id"]: entry for entry in _service().list_characters()}[character_id]

    assert card["name"] == profile.name
    assert card["background_image"] == profile.background_image      # 卡片與預覽同一張背景
    assert "idle" in profile.motions                                  # 舞台 idle
    assert CharacterLibrary().list_action_tags(character_id)          # 舞台動作可解析
    # 每個角色有自己的聲線,不是落到 gender/全域回退
    assert character_id in config.CHARACTER_VOICE_IDS


def test_missing_background_is_reported_instead_of_dropping_the_card(tmp_path, monkeypatch):
    from pet_harness.ui import character_ui_service as module

    profile = type(
        "Profile",
        (),
        {"character_id": "char-Broken", "background_image": "assets/does/not/exist.png", "motions": {}},
    )()
    monkeypatch.setattr(module.CharacterLibrary, "list_action_tags", lambda _self, _cid: [])
    assert module._missing_assets(profile) == ["background_image", "idle_motion", "action_motion"]


@pytest.mark.parametrize("character_id", [*LEGACY_CHARACTER_IDS, *EXPECTED_ROSTER])
def test_legacy_saves_still_resolve_by_their_original_id(character_id):
    """2.4:固定 roster 不改寫資料,舊角色仍由原 ID 解析到自己的 state.db。"""
    profile, _ = CharacterRouter(registry=CharacterRegistry()).load_profile(character_id)
    assert profile.character_id == character_id
    assert profile.sqlite_path == f"data/characters/{character_id}/state.db"


def test_roster_ids_match_the_shipped_manifests():
    for character_id in EXPECTED_ROSTER:
        manifest = json.loads(
            (config.PROJECT_ROOT / "assets" / "characters" / character_id / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["id"] == character_id
        assert manifest.get("is_preset") is True
