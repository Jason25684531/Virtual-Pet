"""任務 3.3:動作解析測試 — 動作只用 router snapshot 的角色,缺動作回同角色 idle,不跨角色 fallback。"""

from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from character_library import CharacterLibrary
from pet_harness.agent.result_parser import ResultParser
from pet_harness.models.events import PetEvent
from ui.transparent_window import TransparentWindow


@pytest.mark.parametrize("value", [None, SimpleNamespace(character_id="Choppr")])
def test_active_character_access_uses_public_adapter_contract(value):
    window = SimpleNamespace(_adapter=SimpleNamespace(
        get_active_snapshot=lambda: value,
        get_active_character=lambda: value,
    ))
    assert TransparentWindow._active_snapshot(window) is value
    assert TransparentWindow._active_character(window) is value


def test_get_current_character_id_reads_router_snapshot():
    fake = MagicMock()
    snapshot = MagicMock()
    snapshot.character_id = "Choppr"
    fake._adapter.get_active_snapshot.return_value = snapshot

    assert TransparentWindow.get_current_character_id(fake) == "Choppr"

    fake._adapter.get_active_snapshot.return_value = None
    assert TransparentWindow.get_current_character_id(fake) is None


def test_restore_current_character_shows_no_active_state_without_snapshot():
    fake = MagicMock()
    fake._adapter.get_active_snapshot.return_value = None

    TransparentWindow._restore_current_character(fake)

    fake._show_no_active_character_state.assert_called_once()
    fake.apply_character.assert_not_called()


def test_restore_current_character_applies_snapshot_character():
    fake = MagicMock()
    snapshot = MagicMock()
    snapshot.character_id = "Choppr"
    fake._adapter.get_active_snapshot.return_value = snapshot
    fake.apply_character.return_value = True

    TransparentWindow._restore_current_character(fake)

    fake.apply_character.assert_called_once_with("Choppr")
    fake._show_no_active_character_state.assert_not_called()


def test_manifest_action_tags_resolve_only_within_the_requested_character():
    library = CharacterLibrary()

    choppr = library.resolve_action_tag("Choppr", "laugh")
    miku = library.resolve_action_tag("miku", "laugh")

    assert choppr and "Choppr" in choppr["path"]
    assert miku and "miku" in miku["path"]
    assert choppr["motion_key"] == miku["motion_key"] == "laugh"
    assert library.resolve_action_tag("Choppr", "play_music") is None
    assert library.resolve_action_tag("Choppr", "not_declared") is None


def test_action_tag_is_structured_and_never_part_of_reply_serialization():
    result = ResultParser().parse(
        '{"reply":"你好", "action_tag":"laugh", "matched_skill":null}',
        provider_type="mock",
    )
    event = PetEvent(
        source_event_id="source-1",
        reply=result.reply,
        matched_skill=None,
        behavior_id="laugh",
        webm_key="laugh",
        xp_delta=0,
        provider_status={},
        saved_to_db=True,
        action_tag=result.action_tag,
        motion_source="action_tag",
    )

    assert result.reply == "你好"
    assert result.action_tag == "laugh"
    assert event.to_dict()["action_tag"] == "laugh"
