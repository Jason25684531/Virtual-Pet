"""P0 baseline: legacy action binding and deferred-dispatch semantics stay stable."""

from unittest.mock import MagicMock

from action_dispatcher import MotionCoordinator


EXPECTED_BINDINGS = {
    "report_news": ("report_news", "room_audio", True, True),
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
