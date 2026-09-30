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


def test_missing_asset_falls_back_to_idle_and_is_not_tracked_as_a_loop_action():
    """align-preset-character-interaction 決策 4：缺素材不再代打其他反應動作，
    一律退回 idle。idle 本來就無限循環、不需要任何人收尾，因此正確地不被
    當成 loop action —— 不會卡在等待一個永遠不會來的 _finish_loop_action()。"""
    window = MagicMock()
    dispatcher = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = (
            lambda motion_key: None if motion_key == "report_news" else f"assets/{motion_key}.webm"
        )
        binding = dispatcher._bindings["report_news"]

        path, kind = dispatcher._resolve_action_motion_path("report_news")
        assert kind == "idle" and path == "assets/idle.webm"

        motion_found = dispatcher._play_binding_motion(binding)

        assert motion_found is True
        window.play_resolved_motion.assert_called_once_with("report_news", "assets/idle.webm", loop=True)
        assert dispatcher._current_loop_action_key is None
        assert dispatcher._current_loop_binding is None
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_true_idle_fallback_is_still_not_tracked_as_a_loop_action():
    """Regression guard: a genuine idle fallback (no dedicated asset for this
    key) must keep its established behaviour untouched — it is not a loop
    action and loops forever by design. No candidate is ever substituted in
    its place (align-preset-character-interaction 決策 4)."""
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


def test_exact_motion_returns_to_idle_once_its_trace_speech_finishes():
    """代打機制已移除(決策 4);改用 report_news 自己有專屬素材的 stub，
    保留原本要保護的行為：TTS 驅動的 loop action 在語音結束後正確回 idle。"""
    window = MagicMock()
    dispatcher = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = lambda motion_key: f"assets/{motion_key}.webm"
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


def test_exact_motion_without_any_speech_still_schedules_protective_cleanup():
    """代打機制已移除(決策 4);改用 report_news 自己有專屬素材的 stub。
    一支沒有任何語音的 loop action(例如選單/快捷鍵觸發、沒有 trace)仍必須排定
    保護性清理計時器：否則沒有 TTS 可以排空，會永遠循環、沒人呼叫
    restore_idle_video()。"""
    from PyQt5.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])  # noqa: F841 - keep alive, else QTimer.start() is a no-op
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = lambda motion_key: f"assets/{motion_key}.webm"

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
