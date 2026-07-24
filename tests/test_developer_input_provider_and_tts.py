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


def _extract_section(html: str, section_id: str) -> str:
    start = html.index(f'id="{section_id}"')
    end = html.index("</section>", start)
    return html[start:end]


def test_skills_and_persona_panels_are_cleanly_separated():
    """Skills 面板規 skills、Persona 面板規 persona；Style/Scene 不含兩者
    (fix-core-interaction-experience)。"""
    html = (Path(__file__).parents[1] / "ui" / "web_container" / "index.html").read_text(encoding="utf-8")

    agent_panel = _extract_section(html, "dock-panel-agent")
    persona_panel = _extract_section(html, "dock-panel-persona")
    style_panel = _extract_section(html, "dock-panel-style")
    scene_panel = _extract_section(html, "dock-panel-scene")

    # Skills 面板容納全部技能管理:清單、別名/優先度、local skill CRUD、命中預覽。
    for marker in ("character-skill-list", "persona-builtin-skill-list", "persona-local-skill-list", "persona-local-skill-form", "persona-preview-input"):
        assert marker in agent_panel, f"expected {marker} inside Skills panel"

    # Persona 面板僅剩人設文字編輯,不含任何技能管理元素。
    assert "persona-textarea" in persona_panel
    for marker in ("persona-builtin-skill-list", "persona-local-skill-list", "persona-local-skill-form", "persona-preview-input", "character-skill-list"):
        assert marker not in persona_panel, f"{marker} leaked into Persona panel"

    # Style / Scene 不含 skills 或 persona 的任何介面元素。
    for panel_html, name in ((style_panel, "Style"), (scene_panel, "Scene")):
        for marker in ("persona-", "character-skill-list"):
            assert marker not in panel_html, f"{marker} leaked into {name} panel"


def test_motion_loop_does_not_restore_idle_until_host_stops_it():
    app_js = (Path(__file__).parents[1] / "ui" / "web_container" / "app.js").read_text(encoding="utf-8")

    assert 'setSource(source, true);' in app_js
    assert 'if (motionLoopActive && motionLoopSource)' in app_js


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
