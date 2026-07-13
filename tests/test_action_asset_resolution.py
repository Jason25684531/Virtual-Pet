"""任務 3.3:動作解析測試 — 動作只用 router snapshot 的角色,缺動作回同角色 idle,不跨角色 fallback。"""

from unittest.mock import MagicMock

from ui.transparent_window import TransparentWindow


def _fake_window(character_id, action_path=None, motion_path=None):
    fake = MagicMock()
    fake.get_current_character_id.return_value = character_id
    fake._library.get_action_motion_path.return_value = action_path
    fake._library.get_motion_path.return_value = motion_path
    fake.change_video.return_value = True
    fake.DEMO_MOTION_MAPPING = TransparentWindow.DEMO_MOTION_MAPPING
    fake.DEMO_ANIMATIONS_DIR = TransparentWindow.DEMO_ANIMATIONS_DIR
    return fake


def test_action_motion_resolves_only_active_snapshot_character():
    fake = _fake_window("Choppr", action_path="assets/webm/characters/Choppr/motions/music_idle.webm")

    assert TransparentWindow.play_action_motion(fake, "music_idle") is True

    # 解析呼叫一律帶 snapshot 的 Choppr,絕不出現其他角色 id
    fake._library.get_action_motion_path.assert_called_once_with("Choppr", "music_idle")
    fake.change_video.assert_called_once()
    assert "Choppr" in fake.change_video.call_args[0][0]


def test_missing_motion_falls_back_to_same_character_idle_not_other_character():
    fake = _fake_window("Choppr", action_path=None, motion_path=None)

    assert TransparentWindow.play_action_motion(fake, "music_idle") is False

    # 缺動作 → 回到同角色 idle;不得播放任何(其他角色的)影片
    fake.restore_idle_video.assert_called_once()
    fake.change_video.assert_not_called()
    for call in fake._library.get_motion_path.call_args_list:
        assert call[0][0] == "Choppr"


def test_get_current_character_id_reads_router_snapshot():
    fake = MagicMock()
    snapshot = MagicMock()
    snapshot.character_id = "Choppr"
    fake._adapter.router.get_active_snapshot.return_value = snapshot

    assert TransparentWindow.get_current_character_id(fake) == "Choppr"

    fake._adapter.router.get_active_snapshot.return_value = None
    assert TransparentWindow.get_current_character_id(fake) is None


def test_restore_current_character_shows_no_active_state_without_snapshot():
    fake = MagicMock()
    fake._adapter.router.get_active_snapshot.return_value = None

    TransparentWindow._restore_current_character(fake)

    fake._show_no_active_character_state.assert_called_once()
    fake.apply_character.assert_not_called()


def test_restore_current_character_applies_snapshot_character():
    fake = MagicMock()
    snapshot = MagicMock()
    snapshot.character_id = "Choppr"
    fake._adapter.router.get_active_snapshot.return_value = snapshot
    fake.apply_character.return_value = True

    TransparentWindow._restore_current_character(fake)

    fake.apply_character.assert_called_once_with("Choppr")
    fake._show_no_active_character_state.assert_not_called()
