"""Characterize loop cleanup before moving dispatch behind the application port."""

from collections import deque
from unittest.mock import MagicMock

from action_dispatcher import DeferredDispatch, MotionCoordinator


def test_loop_cleanup_restores_idle_and_drains_one_deferred_dispatch():
    window = MagicMock()
    coordinator = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        coordinator._current_loop_action_key = "report_news"
        coordinator._current_loop_binding = coordinator._bindings["report_news"]
        coordinator._active_action_trace_id = "active"
        coordinator._deferred_dispatches = deque([
            DeferredDispatch("[ACTION:laugh] later", "next", True),
        ])
        coordinator.dispatch = MagicMock(return_value=True)

        coordinator._finish_loop_action()

        assert coordinator._current_loop_action_key is None
        assert coordinator._current_loop_binding is None
        window.stop_motion_loop.assert_called_once()
        window.clear_panel_video.assert_called_once()
        window.restore_idle_video.assert_called_once()
        coordinator.dispatch.assert_called_once_with(
            "[ACTION:laugh] later", trace_id="next", allow_tts=True
        )
    finally:
        coordinator.shutdown(wait_ms=100)


def test_room_audio_completion_waits_for_main_video_before_cleanup():
    coordinator = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        coordinator._current_loop_action_key = "wave_response"
        coordinator._current_loop_binding = coordinator._bindings["wave_response"]
        coordinator._wait_for_room_audio_ended = True
        coordinator._schedule_loop_cleanup = MagicMock()

        coordinator._on_room_audio_ended()

        assert coordinator._wait_for_room_audio_ended is False
        assert coordinator._wait_for_main_video_ended is True
        coordinator._schedule_loop_cleanup.assert_called_once_with(12000, wait_for_main_video_end=True)
    finally:
        coordinator.shutdown(wait_ms=100)
