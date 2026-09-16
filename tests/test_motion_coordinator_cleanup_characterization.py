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


def test_substitute_motion_for_missing_asset_is_tracked_as_a_real_loop_action():
    """design D3 / fix-play-music-ack-audio-and-idle-restore task 4.2.6: a
    character missing report_news.webm gets a substitute motion (one of the
    general reaction webms). Before the fix this was misclassified the same
    as a true idle fallback, which cleared _current_loop_action_key to None —
    so nothing was ever able to call _finish_loop_action() again and the
    substitute motion looped forever."""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = (
            lambda motion_key: None if motion_key == "report_news" else f"assets/{motion_key}.webm"
        )
        binding = dispatcher._bindings["report_news"]

        path, kind = dispatcher._resolve_action_motion_path("report_news")
        assert kind == "substitute" and path

        motion_found = dispatcher._play_binding_motion(binding)

        assert motion_found is True
        assert dispatcher._current_loop_action_key is not None
        assert dispatcher._current_loop_binding is binding
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_true_idle_fallback_is_still_not_tracked_as_a_loop_action():
    """Regression guard: a genuine idle fallback (no dedicated asset AND no
    substitute pool candidate available either) must keep its established
    behaviour untouched — it is not a loop action and loops forever by design."""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = (
            lambda motion_key: "assets/idle.webm" if motion_key == "idle" else None
        )
        binding = dispatcher._bindings["report_news"]

        path, kind = dispatcher._resolve_action_motion_path("report_news")
        assert kind == "idle" and path == "assets/idle.webm"

        motion_found = dispatcher._play_binding_motion(binding)

        assert motion_found is True
        assert dispatcher._current_loop_action_key is None
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_substitute_motion_returns_to_idle_once_its_trace_speech_finishes():
    window = MagicMock()
    dispatcher = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = (
            lambda motion_key: None if motion_key == "report_news" else f"assets/{motion_key}.webm"
        )
        dispatcher.start_streaming_trace("trace-1")
        dispatcher._trace_pending_tts_counts["trace-1"] = 1

        ok = dispatcher.dispatch(
            "[ACTION:report_news]", trace_id="trace-1", allow_tts=True, wait_for_tts_start=True
        )
        assert ok is True
        dispatcher._on_driver_started("reply-1", "trace-1")  # 語音真的起播

        assert dispatcher._current_loop_action_key is not None

        dispatcher._trace_pending_tts_counts.pop("trace-1")
        dispatcher._completed_tts_traces.add("trace-1")
        dispatcher.finish_streaming_trace("trace-1")

        assert dispatcher._current_loop_action_key is None
        window.restore_idle_video.assert_called_once()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_substitute_motion_without_any_speech_still_schedules_protective_cleanup():
    """A substitute motion dispatched with no speech at all (e.g. a menu/hotkey
    trigger, no trace) must still get a protective cleanup timer scheduled —
    otherwise, with no TTS to ever drain, it loops forever with nobody to call
    restore_idle_video()."""
    from PyQt5.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])  # noqa: F841 - keep alive, else QTimer.start() is a no-op
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = (
            lambda motion_key: None if motion_key == "report_news" else f"assets/{motion_key}.webm"
        )

        ok = dispatcher.dispatch("[ACTION:report_news]", trace_id=None)

        assert ok is True
        assert dispatcher._current_loop_action_key is not None
        assert dispatcher._loop_cleanup_timer is not None
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_room_audio_completion_finishes_motion_only_wave_action():
    coordinator = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        coordinator._current_loop_action_key = "wave_response"
        coordinator._current_loop_binding = coordinator._bindings["wave_response"]
        coordinator._wait_for_room_audio_ended = True
        coordinator._finish_loop_action = MagicMock()

        coordinator._on_room_audio_ended()

        assert coordinator._wait_for_room_audio_ended is False
        coordinator._finish_loop_action.assert_called_once()
    finally:
        coordinator.shutdown(wait_ms=100)
