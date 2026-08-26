"""Regression tests: text submission carries text only, and replies must reach TTS."""

import inspect
import json
from pathlib import Path

import pytest
from unittest.mock import MagicMock

from action_dispatcher import MotionCoordinator
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


def test_personal_edits_apply_on_next_interaction_without_switch(harness_env):
    """personal.json 改動(不經 switch_character)必須在下一次互動套用:persona 進 prompt、alias 可命中。"""
    tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )
    personal_path = tmp_path / "data" / "characters" / "Choppr" / "personal.json"
    personal_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "persona": "HOT_RELOAD_PERSONA",
                "skill_overrides": {"joke_skill": {"aliases": ["講笑話"], "priority": 1}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = adapter.handle_text_input("講笑話")

    assert payload["matched_skill"] == "joke_skill"
    assert "HOT_RELOAD_PERSONA" in (adapter.engine.last_prompt or "")


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


def test_stt_provider_status_reports_none_when_not_wired(harness_env):
    """4.3.3：controller 未注入(或 STT_ENABLED=false)時回報 provider="none"。"""
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    status = adapter.get_provider_status()
    assert status["stt"]["provider"] == "none"

    voice = adapter._build_voice_status()
    assert voice["stt"]["provider"] == "none"
    assert voice["stt"]["required_env"] == ["STT_ENABLED", "STT_MODEL", "STT_DEVICE"]


def test_stt_provider_status_reports_faster_whisper_when_wired(harness_env):
    """4.3.3：controller 已接線且非 unavailable 時回報 provider="faster_whisper"。"""
    from pet_harness.voice_runtime_status_adapter import VoiceRuntimeStatusAdapter

    _tmp_path, agentic_root = harness_env
    fake_controller = MagicMock()
    fake_controller.state = "idle"
    fake_controller.is_listening = False
    fake_controller.last_error = ""
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
        voice_status_adapter=VoiceRuntimeStatusAdapter(stt_controller=fake_controller),
    )

    status = adapter.get_provider_status()
    assert status["stt"]["provider"] == "faster_whisper"

    voice = adapter._build_voice_status()
    assert voice["stt"]["provider"] == "faster_whisper"


def test_on_agentic_result_speaks_nonempty_reply():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = "music_idle"

    TransparentWindow.consume_interaction_result(fake_self, {"reply": "hello there", "webm_key": "music_idle"})

    # harness motion key 一律走 dispatch_action 的 TTS 同步機制:
    # 動畫由 TTS 起播觸發、維持到同輪 TTS 播畢(queue_drained)才回 idle。
    fake_self.dispatch_action.assert_called_once()
    args, kwargs = fake_self.dispatch_action.call_args
    assert args == ("[ACTION:music_idle] hello there",)
    assert kwargs["allow_tts"] is True
    assert kwargs["wait_for_tts_start"] is True
    assert kwargs["trace_id"]  # non-empty trace_id required by PCM session playback
    fake_self.play_action_motion.assert_not_called()
    fake_self.speak_text.assert_not_called()


def test_on_streaming_result_does_not_enqueue_final_reply_again():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = ""

    TransparentWindow.consume_interaction_result(
        fake_self,
        {
            "reply": "First sentence. Second sentence.",
            "webm_key": "",
            "raw_event": {
                "metadata": {"agentic": {"streaming": True}},
            },
        },
    )

    fake_self.speak_text.assert_not_called()
    fake_self.dispatch_action.assert_not_called()


def test_streaming_result_dispatches_final_action_without_repeating_tts():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = "laugh"

    TransparentWindow.consume_interaction_result(
        fake_self,
        {
            "reply": "已逐句播放的回覆。",
            "action_tag": "laugh",
            "webm_key": "laugh",
            "metadata": {"agentic": {"streaming": True}},
        },
    )

    fake_self.dispatch_action.assert_called_once()
    args, kwargs = fake_self.dispatch_action.call_args
    assert args == ("[ACTION:laugh]",)
    assert kwargs["allow_tts"] is True
    assert kwargs["wait_for_tts_start"] is True
    fake_self.speak_text.assert_not_called()


def test_on_agentic_result_skips_empty_reply():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = ""

    TransparentWindow.consume_interaction_result(fake_self, {"reply": "   ", "webm_key": ""})

    fake_self.speak_text.assert_not_called()


def test_on_agentic_result_speaks_reply_when_motion_dispatch_is_rejected():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = "music_idle"
    fake_self.dispatch_action.return_value = False

    TransparentWindow.consume_interaction_result(fake_self, {"reply": "工具完成後的內容", "webm_key": "music_idle"})

    fake_self.speak_text.assert_called_once()
    assert fake_self.speak_text.call_args.args[0] == "工具完成後的內容"


def test_on_agentic_result_starts_a_latency_trace():
    fake_self = MagicMock()
    fake_self._validated_event_motion_key.return_value = ""
    fake_self._latency_tracker.begin_interaction.return_value = "measured-trace"

    TransparentWindow.consume_interaction_result(fake_self, {"reply": "回覆", "user_text": "問題"})

    fake_self._latency_tracker.begin_interaction.assert_called_once_with("harness", "問題")
    assert fake_self.speak_text.call_args.kwargs["trace_id"] == "measured-trace"


def test_harness_reply_does_not_suppress_tts_for_wave_response():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._play_binding_motion = MagicMock(return_value=True)

        dispatcher.dispatch("[ACTION:wave_response] 實際的模型回覆", trace_id="trace-1")

        assert "trace-1" not in dispatcher._tts_not_expected_traces
        assert dispatcher._loop_action_service_pending is False
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_chat_input_lives_in_the_summonable_chat_hud():
    html = (Path(__file__).parents[1] / "ui" / "web_container" / "index.html").read_text(encoding="utf-8")

    chat_hud = _extract_section(html, "hud-chat")
    assert html.count('id="conversation-list"') == 1
    assert 'id="talk-text-input"' in chat_hud
    assert 'conversation-history-panel' not in html


def _extract_section(html: str, section_id: str) -> str:
    start = html.index(f'id="{section_id}"')
    end = html.index("</section>", start)
    return html[start:end]


def test_developer_controls_are_hidden_in_the_debug_panel():
    """CAC UX keeps skill-CRUD developer controls out of end-user HUDs.

    人設欄位是例外：使用者要求它移入 Agent HUD 本體，不再只藏在除錯面板裡
    （見 test_persona_editor_lives_in_the_agent_hud）。"""
    html = (Path(__file__).parents[1] / "ui" / "web_container" / "index.html").read_text(encoding="utf-8")

    debug_start = html.index('id="debug-panel"')
    debug_panel = html[debug_start:html.index("</aside>", debug_start)]
    for marker in ("character-skill-list", "persona-builtin-skill-list", "persona-local-skill-list", "persona-local-skill-form", "persona-preview-input"):
        assert marker in debug_panel, f"expected {marker} inside Debug panel"
    assert "persona-textarea" not in debug_panel
    assert 'id="debug-panel" hidden' in html
    for hud_id in ("hud-style", "hud-scene"):
        hud_html = _extract_section(html, hud_id)
        assert "persona-" not in hud_html
        assert "character-skill-list" not in hud_html


def test_persona_editor_lives_in_the_agent_hud():
    """人設編輯原本只藏在 Ctrl+Shift+D 的除錯面板裡，一般使用流程完全看不到。
    移入 Agent HUD 本體，沿用既有 bridge（見 loadPersonaEditor／savePersonaDraft）。"""
    html = (Path(__file__).parents[1] / "ui" / "web_container" / "index.html").read_text(encoding="utf-8")

    agent_html = _extract_section(html, "hud-agent")
    for marker in ("persona-textarea", "persona-save-button", "persona-cancel-button", "persona-validation-status"):
        assert marker in agent_html, f"expected {marker} inside Agent HUD"


def test_motion_loop_does_not_restore_idle_until_host_stops_it():
    app_js = (Path(__file__).parents[1] / "ui" / "web_container" / "app.js").read_text(encoding="utf-8")

    start = app_js.index('window.startMotionLoop')
    loop_block = app_js[start:app_js.index('window.stopMotionLoop = function', start)]
    assert 'setSource(source, false);' in loop_block
    assert 'replayMotionLoop(loopGeneration);' in loop_block
    assert 'window.playTemporaryVideo(motionLoopSource);' not in loop_block
    assert 'if (motionLoopActive && motionLoopSource && (video.ended || (video.paused && !video.seeking && video.readyState >= 2)))' in app_js


def test_motion_loop_replay_does_not_reload_the_same_source():
    app_js = (Path(__file__).parents[1] / "ui" / "web_container" / "app.js").read_text(encoding="utf-8")

    start = app_js.index('window.startMotionLoop')
    stop = app_js.index('window.stopMotionLoop = function', start)
    loop_block = app_js[start:stop]
    replay_start = app_js.index('function replayMotionLoop')
    replay_block = app_js[replay_start:app_js.index('window.startMotionLoop', replay_start)]

    assert 'video.src' not in replay_block
    assert 'video.load()' not in replay_block
    assert 'loopGeneration !== motionLoopGeneration' in replay_block
    assert '!motionLoopActive' in replay_block


def test_action_waits_for_audio_driver_when_tts_sync_is_requested():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
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


def test_late_streaming_action_starts_for_an_already_playing_tts_trace():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        binding = dispatcher._bindings["laugh"]
        dispatcher._driver_started_pairs.add(("reply-1", "trace-1"))
        dispatcher._play_binding_motion = MagicMock(return_value=True)

        dispatcher._start_pending_action("trace-1", binding, wait_for_tts_start=True)

        assert dispatcher._play_binding_motion.called
        assert dispatcher._pending_actions["trace-1"].has_tts is True
        assert dispatcher._loop_action_tts_queued is True
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_activation_detects_tts_queued_before_the_action_was_dispatched():
    """Streamed TTS chunks can be enqueued for a trace before its [ACTION:x]
    tag is dispatched (no PendingActionState exists yet at queue time), so
    state.has_tts alone must not gate _loop_action_tts_queued or the loop
    would wait forever for a completion signal that already happened."""
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = MagicMock(return_value="fake/wave_response.webm")
        binding = dispatcher._bindings["wave_response"]
        dispatcher._start_pending_action("trace-1", binding, wait_for_tts_start=True)
        dispatcher._trace_pending_tts_counts["trace-1"] = 1  # queued before activation

        dispatcher._activate_pending_action("trace-1")

        assert dispatcher._loop_action_tts_queued is True
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_action_loop_waits_for_all_tts_segments_of_its_trace():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._current_loop_action_key = "laugh"
        dispatcher._current_loop_binding = dispatcher._bindings["laugh"]
        dispatcher._active_action_trace_id = "trace-1"
        dispatcher._loop_action_tts_queued = True
        dispatcher._trace_pending_tts_counts["trace-1"] = 1
        dispatcher._finish_loop_action = MagicMock()

        dispatcher._finish_loop_action_if_tts_idle()

        dispatcher._finish_loop_action.assert_not_called()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_action_loop_waits_until_its_streaming_trace_is_closed():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._current_loop_action_key = "laugh"
        dispatcher._current_loop_binding = dispatcher._bindings["laugh"]
        dispatcher._active_action_trace_id = "trace-1"
        dispatcher._loop_action_tts_queued = True
        dispatcher._streaming_traces = {"trace-1"}
        dispatcher._finish_loop_action = MagicMock()

        dispatcher._finish_loop_action_if_tts_idle()

        dispatcher._finish_loop_action.assert_not_called()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_action_loop_waits_while_audio_worker_is_busy():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._current_loop_action_key = "laugh"
        dispatcher._current_loop_binding = dispatcher._bindings["laugh"]
        dispatcher._active_action_trace_id = "trace-1"
        dispatcher._loop_action_tts_queued = True
        dispatcher._audio_worker.is_busy = MagicMock(return_value=True)
        dispatcher._finish_loop_action = MagicMock()

        dispatcher._finish_loop_action_if_tts_idle()
        dispatcher._finish_loop_action.assert_not_called()

        dispatcher._audio_worker.is_busy.return_value = False
        dispatcher._finish_loop_action_if_tts_idle()
        dispatcher._finish_loop_action.assert_called_once()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_closing_streaming_trace_finishes_an_idle_action_loop():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._current_loop_action_key = "laugh"
        dispatcher._current_loop_binding = dispatcher._bindings["laugh"]
        dispatcher._active_action_trace_id = "trace-1"
        dispatcher._loop_action_tts_queued = True
        dispatcher._streaming_traces = {"trace-1"}
        dispatcher._finish_loop_action = MagicMock()

        dispatcher.finish_streaming_trace("trace-1")

        dispatcher._finish_loop_action.assert_called_once()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_harness_motion_key_outside_whitelist_uses_tts_synced_binding():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = MagicMock(return_value="assets/music_idle.webm")

        ok = dispatcher.dispatch(
            "[ACTION:music_idle] 好的，來點音樂",
            trace_id="trace-1",
            allow_tts=True,
            wait_for_tts_start=True,
        )

        # 不再被 9-動作白名單吞掉:建立 pending action,動畫等 TTS 起播、
        # 播畢(queue_drained)才回 idle。
        assert ok is True
        state = dispatcher._pending_actions["trace-1"]
        assert state.binding.motion_key == "music_idle"
        assert state.wait_for_tts_start is True
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_unknown_action_without_motion_file_still_fails_closed():
    window = MagicMock()
    dispatcher = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        dispatcher._find_motion_path = MagicMock(return_value=None)

        ok = dispatcher.dispatch("[ACTION:no_such_motion] hi", trace_id="trace-1")

        assert ok is False
        window.restore_idle_video.assert_called_once()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_final_tts_segment_closes_trace_so_motion_can_return_to_idle():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
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
