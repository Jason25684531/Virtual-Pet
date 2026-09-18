"""P0 baseline: legacy action binding and deferred-dispatch semantics stay stable."""

import logging
from unittest.mock import MagicMock

from action_dispatcher import MotionCoordinator


EXPECTED_BINDINGS = {
    "report_news": ("report_news", "default", True, True),
    "play_music": ("play_music", "panel_video", True, True),
    "wave_response": ("wave_response", "default", False, False),
    "laugh": ("laugh", "default", False, False),
    "angry": ("angry", "default", False, False),
    "awkward": ("awkward", "default", False, False),
    "speechless": ("speechless", "default", False, False),
    "listen": ("listen", "default", False, False),
    "idle": ("idle", "default", False, False),
    "cached_joke": ("laugh", "room_audio", True, True),
    "cached_share": ("listen", "room_audio", True, True),
}


def test_action_binding_table_and_deferred_dispatch_contract():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        actual = {
            name: (binding.motion_key, binding.finish_event, binding.non_repeatable,
                   binding.blocks_following_dispatch)
            for name, binding in dispatcher._bindings.items()
        }
        assert actual == EXPECTED_BINDINGS

        dispatcher._current_loop_binding = dispatcher._bindings["report_news"]
        dispatcher._current_loop_action_key = "report_news"

        assert dispatcher.dispatch("[ACTION:laugh] later", trace_id="next-turn") is True
        assert [item.directive for item in dispatcher._deferred_dispatches] == ["[ACTION:laugh] later"]
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_streaming_ack_action_tag_no_longer_self_suppresses_its_own_speech():
    """Root cause repro (fix-play-music-ack-audio-and-idle-restore): mirrors
    ui/transparent_window.py consume_interaction_result's streaming branch —
    the ack text is queued for TTS first, then the harness-resolved behavior's
    [ACTION:play_music] is dispatched for the SAME trace with no display_message
    and wait_for_tts_start=True. harness_reply used to be computed from "did this
    call carry text", which is False here even though harness owns the turn —
    so the skip_tts_sync fast path suppressed the turn's own just-queued ack audio.
    Before the fix this test fails: _suppressed_traces gains "trace-1" and
    suppress_trace() is called on it."""
    music_factory = MagicMock()
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False, music_worker_factory=music_factory)
    try:
        dispatcher._find_motion_path = MagicMock(return_value="assets/play_music.webm")
        dispatcher._audio_worker.suppress_trace = MagicMock()
        dispatcher.start_streaming_trace("trace-1")
        dispatcher._trace_pending_tts_counts["trace-1"] = 1  # ack TTS already enqueued for this trace

        ok = dispatcher.dispatch(
            "[ACTION:play_music]", trace_id="trace-1", allow_tts=True, wait_for_tts_start=True
        )

        assert ok is True
        assert "trace-1" not in dispatcher._suppressed_traces
        dispatcher._audio_worker.suppress_trace.assert_not_called()
        music_factory.assert_not_called()
        state = dispatcher._pending_actions["trace-1"]
        assert state.wait_for_tts_start is True
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_non_streaming_play_music_action_still_suppresses_audio_when_it_has_no_speech_of_its_own():
    """The legacy skip_tts_sync fast path (hotkey/menu play_music, no harness reply
    text, no streaming trace) must keep suppressing stale audio when the trace truly
    has no speech of its own — this is not the bug and must not regress."""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = MagicMock(return_value="assets/play_music.webm")
        dispatcher._audio_worker.suppress_trace = MagicMock()

        ok = dispatcher.dispatch("[ACTION:play_music]", trace_id="trace-1", allow_tts=True)

        assert ok is True
        assert "trace-1" in dispatcher._suppressed_traces
        dispatcher._audio_worker.suppress_trace.assert_called_once_with("trace-1")
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_skip_tts_sync_guard_does_not_leak_into_hotkey_trace_that_already_has_speech():
    """Defensive guard (design D2): even without _streaming_traces, a trace that
    already has its own speech queued must not be self-suppressed by a skip_tts_sync
    binding."""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = MagicMock(return_value="assets/play_music.webm")
        dispatcher._audio_worker.suppress_trace = MagicMock()
        dispatcher._trace_pending_tts_counts["trace-1"] = 1

        ok = dispatcher.dispatch("[ACTION:play_music]", trace_id="trace-1", allow_tts=True)

        assert ok is True
        assert "trace-1" not in dispatcher._suppressed_traces
        dispatcher._audio_worker.suppress_trace.assert_not_called()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_same_trace_repeated_action_tag_is_deduped_not_deferred():
    """Streamed replies can repeat the same [ACTION:x] tag across sentence chunks.

    Re-dispatching the still-active action for the same trace must not restart
    start_motion_loop (it would hard-reload the WebM and reset
    _loop_action_tts_queued), but a different trace's dispatch must proceed.
    """
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        binding = dispatcher._bindings["awkward"]
        dispatcher._current_loop_binding = binding
        dispatcher._active_action_trace_id = "turn-1"

        assert dispatcher._is_duplicate_loop_action(binding, "turn-1") is True
        assert dispatcher._is_duplicate_loop_action(binding, "turn-2") is False
        assert dispatcher._is_duplicate_loop_action(binding, None) is False
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_missing_motion_asset_falls_back_to_idle_without_substituting(caplog):
    """缺素材一律回 idle,不隨機代打其他反應動作(align-preset-character-interaction
    決策 4;與 voice-motion-sync 的「動作影片缺失時維持閒置」要求一致)。"""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = (
            lambda motion_key: None if motion_key == "report_news" else f"assets/{motion_key}.webm"
        )

        with caplog.at_level(logging.WARNING, logger="action_dispatcher"):
            path, kind = dispatcher._resolve_action_motion_path("report_news")

        assert kind == "idle"
        assert path == "assets/idle.webm"
        assert any(
            "找不到動作檔案" in record.message and "report_news" in record.message
            for record in caplog.records
        )
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_timeout_promoted_and_critical_failure_are_stored_as_distinct_reasons():
    """design D5 (fix-play-music-ack-audio-and-idle-restore 4.2.10):
    _suppressed_traces 記下真實原因,而不是單一 bool。"""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = MagicMock(return_value="assets/laugh.webm")
        binding = dispatcher._bindings["laugh"]
        dispatcher._start_pending_action("trace-1", binding, wait_for_tts_start=False)
        dispatcher._promote_pending_action("trace-1")

        assert dispatcher._suppressed_traces["trace-1"] == "timeout_promoted"

        dispatcher._handle_critical_tts_failure("trace-2", "boom")

        assert dispatcher._suppressed_traces["trace-2"] == "critical_tts_failure"
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_timeout_promoted_and_critical_failure_log_distinct_suppression_messages(caplog):
    """修正前 _on_tts_finished 一律寫死「因 timeout_promoted 抑制晚到音訊」;
    critical_tts_failure 造成的晚到語音也會被誤報成 timeout_promoted。"""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._suppressed_traces["trace-timeout"] = "timeout_promoted"
        dispatcher._suppressed_traces["trace-critical"] = "critical_tts_failure"

        with caplog.at_level(logging.WARNING, logger="tts_playback"):
            dispatcher._on_tts_finished(
                "reply-1", True, "", {"trace_id": "trace-timeout"},
            )
            dispatcher._on_tts_finished(
                "reply-2", True, "", {"trace_id": "trace-critical"},
            )

        messages = [record.message for record in caplog.records]
        timeout_message = next(m for m in messages if "timeout_promoted" in m)
        critical_message = next(m for m in messages if "語音服務失敗" in m)
        assert "timeout_promoted" not in critical_message
        assert timeout_message != critical_message
    finally:
        dispatcher.shutdown(wait_ms=100)
