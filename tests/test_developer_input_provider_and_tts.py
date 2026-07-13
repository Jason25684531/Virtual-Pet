"""Regression tests: text submission carries text only, and replies must reach TTS."""

import inspect
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from action_dispatcher import ActionDispatcher
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from ui.transparent_window import TransparentWindow

from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)
from tests.conftest import FakeProvider
from pet_harness.runtime.provider_runtime import ProviderRuntime


def test_handle_text_input_accepts_text_only(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    # 提交簽名不得再接受 provider 覆寫參數
    assert list(inspect.signature(adapter.handle_text_input).parameters) == ["text"]

    payload = adapter.handle_text_input("hello")
    assert payload["reply"].startswith("[fake]")
    assert payload["user_text"] == "hello"


def test_handle_text_input_rejects_provider_keyword(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    with pytest.raises(TypeError):
        adapter.handle_text_input("hello", provider="api")


def test_get_provider_status_does_not_crash(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    status = adapter.get_provider_status()
    assert status["active_character_id"] == "Choppr"
    assert status["ai"]["provider"] is not None
    assert "tts" in status and "stt" in status


def test_on_agentic_result_speaks_nonempty_reply():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = "idle"

    TransparentWindow.consume_interaction_result(fake_self, {"reply": "hello there", "webm_key": "idle"})

    fake_self.dispatch_action.assert_called_once()
    args, kwargs = fake_self.dispatch_action.call_args
    assert args == ("[ACTION:idle] hello there",)
    assert kwargs["allow_tts"] is True
    assert kwargs["wait_for_tts_start"] is True
    assert kwargs["trace_id"]  # non-empty trace_id required by PCM session playback
    fake_self.speak_text.assert_not_called()


def test_on_agentic_result_skips_empty_reply():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = ""

    TransparentWindow.consume_interaction_result(fake_self, {"reply": "   ", "webm_key": ""})

    fake_self.speak_text.assert_not_called()


def test_history_panel_is_independent_of_talk_and_skills_docks():
    html = (Path(__file__).parents[1] / "ui" / "web_container" / "index.html").read_text(encoding="utf-8")

    assert html.index('id="conversation-history-panel"') < html.index('id="companion-dock-root"')
    assert html.count('id="conversation-panel"') == 1
    assert html.index('id="talk-text-input"') < html.index('id="companion-dock-root"')
    assert 'id="dock-panel-talk"' not in html


def test_motion_loop_does_not_restore_idle_until_host_stops_it():
    app_js = (Path(__file__).parents[1] / "ui" / "web_container" / "app.js").read_text(encoding="utf-8")

    assert 'setSource(source, true);' in app_js
    assert 'if (motionLoopActive && motionLoopSource)' in app_js


def test_action_waits_for_audio_driver_when_tts_sync_is_requested():
    dispatcher = ActionDispatcher(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        binding = dispatcher._bindings["laugh"]
        dispatcher._start_pending_action("trace-1", binding, wait_for_tts_start=True)
        dispatcher._play_binding_motion = MagicMock(return_value=True)

        state = dispatcher._pending_actions["trace-1"]
        assert state.wait_for_tts_start is True
        assert state.timeout_timer is None
        dispatcher._play_binding_motion.assert_not_called()

        dispatcher._on_driver_started("reply-1", "trace-1")

        dispatcher._play_binding_motion.assert_called_once_with(binding)
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_final_tts_segment_closes_trace_so_motion_can_return_to_idle():
    dispatcher = ActionDispatcher(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._audio_worker.close_trace_session = MagicMock()
        dispatcher._trace_pending_tts_counts["trace-1"] = 1

        dispatcher._on_tts_finished(
            "reply-1",
            True,
            "queued",
            {"trace_id": "trace-1", "queued_playback": True},
        )

        dispatcher._audio_worker.close_trace_session.assert_called_once_with("trace-1")
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_invalid_action_tag_restores_idle_without_cross_character_playback():
    fake_self = MagicMock()
    fake_self.get_current_character_id.return_value = "Choppr"
    fake_self._library.resolve_action_tag.return_value = None

    motion_key = TransparentWindow._validated_event_motion_key(
        fake_self,
        {"action_tag": "foreign_motion", "webm_key": "laugh"},
    )

    assert motion_key == ""
    fake_self._library.resolve_action_tag.assert_called_once_with("Choppr", "foreign_motion")
    fake_self.restore_idle_video.assert_called_once()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
