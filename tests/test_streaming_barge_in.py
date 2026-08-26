import json
import threading
from pathlib import Path

import pytest

from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.engine.harness_engine import PetHarnessEngine, _SentenceSplitter, _StreamingReplyExtractor
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.app.action_bus import ActionBus
from pet_harness.app.commands import ActionCommand
from pet_harness.app.event_bus import SimpleEventBus
from pet_harness.app.handlers import ConversationHandler
from pet_harness.app.ports import PreparedTurn
from tests.conftest import FakeProvider
from unittest.mock import MagicMock
from action_dispatcher import MotionCoordinator


@pytest.fixture
def streaming_env(tmp_path, monkeypatch):
    assets = tmp_path / "assets" / "webm" / "characters" / "Choppr"
    assets.mkdir(parents=True)
    (assets / "manifest.json").write_text(json.dumps({
        "id": "Choppr", "name": "Choppr", "background_image": "",
        "motions_dir": "assets/webm/characters/Choppr/motions", "motions": {},
        "idle_pool": [], "voice_id_env_key": "", "layout": {},
    }), encoding="utf-8")
    data = tmp_path / "data" / "characters" / "Choppr"
    data.mkdir(parents=True)
    (data / "profile.json").write_text(json.dumps({
        "persona_description": "Choppr persona", "skill_config": [],
    }), encoding="utf-8")
    skills = tmp_path / ".agentic" / "skills"
    skills.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    from pet_harness.character import profile as profile_module
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    return tmp_path, tmp_path / ".agentic"


def test_sentence_splitter_strips_only_the_first_action_tag():
    splitter = _SentenceSplitter()

    assert splitter.feed("[ACTION:laugh] First.") == ["First."]
    assert splitter.actions == ["laugh"]
    assert splitter.feed(" Second") == []
    assert splitter.flush() == ["Second"]


def test_streaming_reply_extractor_sends_only_json_reply_to_tts():
    extractor = _StreamingReplyExtractor()
    output = []
    for fragment in ['{"re', 'ply":"你好，', '世界。",', '"confidence":0.9}']:
        output.append(extractor.feed(fragment))
    output.append(extractor.flush())

    assert "".join(output) == "你好，世界。"


def test_streaming_reply_extractor_preserves_plain_text_and_action_prefix():
    extractor = _StreamingReplyExtractor()
    assert extractor.feed("[ACTION:laugh] First sentence.") == "[ACTION:laugh] First sentence."
    assert extractor.flush() == ""


def test_ollama_stream_uses_iter_lines_and_honors_cancel():
    calls = []

    class Response:
        status_code = 200

        def iter_lines(self):
            yield json.dumps({"response": "one"}).encode()
            yield json.dumps({"response": "two", "done": True}).encode()

        def close(self):
            calls.append("closed")

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs["json"]["stream"], kwargs["stream"]))
        return Response()

    provider = OllamaProvider(
        ProviderConfig(provider_type=ProviderType.OLLAMA, model_name="test", base_url="http://ollama"),
        request_fn=request,
    )
    assert list(provider.generate_reply_stream(type("Event", (), {"text": "hello"})())) == ["one", "two"]
    assert calls[0] == ("POST", "http://ollama/api/generate", True, True)
    assert calls[-1] == "closed"


class _StreamingProvider(FakeProvider):
    def generate_reply_stream(self, event, matched_skill=None, prompt_text=None, cancel=None):
        yield "[ACTION:laugh] First sentence. Second sentence."

    def get_status(self):
        return ProviderStatus(provider_type=ProviderType.OLLAMA, healthy=True, message="streaming")


class _JsonStreamingProvider(_StreamingProvider):
    def generate_reply_stream(self, event, matched_skill=None, prompt_text=None, cancel=None):
        for fragment in ('{"reply":"First sentence. ', 'Second sentence.","confidence":0.9}'):
            yield fragment


class _StreamUnavailableProvider(FakeProvider):
    """Simulates ProviderRuntime: generate_reply_stream is always present (per the
    LLMProviderAdapter protocol's optional-return contract) but returns None when the
    wrapped provider (e.g. APIProvider/GPT-4o) doesn't actually support streaming."""

    def generate_reply_stream(self, event, matched_skill=None, prompt_text=None, cancel=None):
        return None


class _CancelingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.cancel = None

    def generate_reply_stream(self, event, matched_skill=None, prompt_text=None, cancel=None):
        self.cancel = cancel
        yield "First sentence."
        self.cancel.set()
        yield "Never spoken."


def test_cancelled_stream_without_spoken_chunk_is_stale(streaming_env):
    tmp_path, agentic_root = streaming_env
    engine = PetHarnessEngine(
        provider=_CancelingProvider(), agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "stale.json", character_id="Choppr",
    )
    event = engine.handle_event({"text": "interrupt"}, stream_callback=lambda _chunk: None)

    assert event.metadata["stale_turn"] is True
    assert event.saved_to_db is False
    assert engine.recent_events() == []


def test_cancelled_stream_with_spoken_chunk_persists_only_spoken_text(streaming_env):
    tmp_path, agentic_root = streaming_env
    engine = PetHarnessEngine(
        provider=_CancelingProvider(), agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "spoken.json", character_id="Choppr",
    )
    event = engine.handle_event(
        {"text": "interrupt"},
        stream_callback=lambda chunk: engine.mark_spoken_chunk(chunk),
    )

    assert event.saved_to_db is True
    assert event.reply == "First sentence."
    assert "Never spoken." not in engine.recent_events()[0]["output_payload"]["reply"]


def test_interrupt_trace_suppresses_audio_and_clears_active_motion():
    window = MagicMock()
    dispatcher = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        dispatcher._active_action_trace_id = "trace-1"
        dispatcher._current_loop_action_key = "laugh"
        dispatcher._current_loop_binding = dispatcher._bindings["laugh"]
        dispatcher.interrupt_trace("trace-1")

        assert "trace-1" in dispatcher._suppressed_traces
        assert dispatcher._active_action_trace_id is None
        assert dispatcher._current_loop_action_key is None
        window.restore_idle_video.assert_called()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_interrupt_all_suppresses_completed_tts_trace_and_restores_idle():
    window = MagicMock()
    dispatcher = MotionCoordinator(window, MagicMock(), tts_enabled=False)
    try:
        dispatcher._completed_tts_traces.add("trace-1")
        dispatcher._panel_video_started = True
        dispatcher._panel_video_ended = True
        dispatcher._wait_for_room_audio_ended = True
        dispatcher._loop_action_service_pending = True
        dispatcher._audio_worker.interrupt_all = MagicMock()

        dispatcher.interrupt_all()

        assert "trace-1" in dispatcher._suppressed_traces
        dispatcher._audio_worker.interrupt_all.assert_called_once()
        window.stop_music.assert_called_once()
        window.clear_panel_video.assert_called_once()
        assert dispatcher._panel_video_started is False
        assert dispatcher._panel_video_ended is False
        assert dispatcher._wait_for_room_audio_ended is False
        assert dispatcher._loop_action_service_pending is False
        window.restore_idle_video.assert_called()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_interrupt_all_stops_delayed_news_action():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        news_timer = MagicMock()
        dispatcher._news_audio_delay_timer = news_timer

        dispatcher.interrupt_all()

        news_timer.stop.assert_called_once()
        assert dispatcher._news_audio_delay_timer is None
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_streaming_wave_action_is_motion_only():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher._handle_motion_only = MagicMock()

        dispatcher.dispatch("[ACTION:wave_response]", trace_id="trace-1", allow_tts=False)

        dispatcher._handle_motion_only.assert_called_once()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_stream_action_waits_for_its_tts_before_starting_the_motion_loop():
    dispatcher = MotionCoordinator(MagicMock(), MagicMock(), tts_enabled=False)
    try:
        dispatcher.dispatch = MagicMock()

        dispatcher._dispatch_stream_action("laugh", "trace-1")

        dispatcher.dispatch.assert_called_once_with(
            "[ACTION:laugh]", trace_id="trace-1", allow_tts=True, wait_for_tts_start=True
        )
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_stream_action_with_active_tts_does_not_arm_the_3s_cleanup(tmp_path):
    """Regression: 串流回合的 [ACTION:x] 不帶文字，dispatch 尾端曾把它當
    non-TTS 動作排 3 秒無條件 _finish_loop_action，語音還在播動畫就被收掉。
    同 trace 已有 TTS 活動時不得排該清理，收尾交給 queue_drained。"""
    webm = tmp_path / "laugh.webm"
    webm.write_bytes(b"")
    dispatcher = MotionCoordinator(
        MagicMock(), MagicMock(), tts_enabled=False,
        motion_path_resolver=lambda key: str(webm),
    )
    try:
        dispatcher._schedule_non_tts_loop_cleanup = MagicMock()
        # 模擬串流情境：action tag 抵達前，同 trace 的 TTS 已起播且仍有待播 chunk。
        dispatcher._driver_started_pairs.add(("reply-1", "trace-1"))
        dispatcher._trace_pending_tts_counts["trace-1"] = 1

        dispatcher.dispatch("[ACTION:laugh]", trace_id="trace-1", allow_tts=True, wait_for_tts_start=True)

        assert dispatcher._current_loop_action_key == "laugh"
        assert dispatcher._loop_action_tts_queued is True
        dispatcher._schedule_non_tts_loop_cleanup.assert_not_called()
    finally:
        dispatcher.shutdown(wait_ms=100)


def test_engine_streams_ordered_sentence_callbacks_and_keeps_fallback_provider_path(streaming_env):
    tmp_path, agentic_root = streaming_env
    engine = PetHarnessEngine(
        provider=_StreamingProvider(),
        agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "event.json",
        character_id="Choppr",
    )
    chunks, actions = [], []
    event = engine.handle_event(
        {"text": "hello"},
        stream_callback=chunks.append,
        action_callback=actions.append,
    )

    assert chunks == ["First sentence.", "Second sentence."]
    assert actions == ["laugh"]
    assert event.reply.startswith("[ACTION:laugh]")

    fallback_chunks = []
    fallback_engine = PetHarnessEngine(
        provider=FakeProvider(),
        agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "fallback.json",
        character_id="Choppr",
    )
    fallback_engine.handle_event({"text": "fallback"}, stream_callback=fallback_chunks.append)
    assert fallback_chunks == []


def test_engine_streams_only_json_reply_to_tts_callbacks(streaming_env):
    tmp_path, agentic_root = streaming_env
    engine = PetHarnessEngine(
        provider=_JsonStreamingProvider(),
        agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "json-event.json",
        character_id="Choppr",
    )
    chunks = []
    event = engine.handle_event({"text": "hello"}, stream_callback=chunks.append)

    assert chunks == ["First sentence.", "Second sentence."]
    assert event.reply == "First sentence. Second sentence."


def test_engine_falls_back_to_blocking_reply_without_dropping_timeline_when_stream_returns_none(streaming_env):
    """Regression: the fallback used to recurse via `self._invoke_provider(event, skill, prompt)`,
    a bare positional call that dropped timeline/stream_callback/action_callback/cancel — silently
    breaking llm_ttft/llm_done instrumentation and barge-in cancellation the moment a provider's
    generate_reply_stream legitimately returns None (e.g. ProviderRuntime wrapping GPT-4o's
    non-streaming APIProvider). This never triggered against Ollama, whose real stream never
    returns None, so it stayed invisible until a non-streaming provider was configured."""
    tmp_path, agentic_root = streaming_env
    engine = PetHarnessEngine(
        provider=_StreamUnavailableProvider(),
        agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "no-stream-event.json",
        character_id="Choppr",
    )
    chunks = []
    event = engine.handle_event({"text": "hello"}, stream_callback=chunks.append)

    assert event.reply.startswith("[fake]")
    latency = event.metadata["latency"]
    assert latency["llm_ttft_ms"] is not None
    assert latency["timeline_complete"] is True
    assert latency["missing_checkpoints"] == []


def test_engine_marks_llm_first_token_on_the_raw_provider_fragment_and_logs_streaming_diagnostic(streaming_env, caplog):
    """llm_first_token must fire on the provider's first raw fragment (real streaming
    diagnostics), not on the first fully-split sentence or the completed response."""
    import logging

    tmp_path, agentic_root = streaming_env
    engine = PetHarnessEngine(
        provider=_StreamingProvider(),
        agentic_root=agentic_root,
        snapshot_path=tmp_path / "debug" / "ttft-event.json",
        character_id="Choppr",
    )
    with caplog.at_level(logging.INFO, logger="pet_harness.engine.harness_engine"):
        event = engine.handle_event({"text": "hello"}, stream_callback=lambda _c: None)

    latency = event.metadata["latency"]
    assert latency["llm_ttft_ms"] is not None
    assert latency["timeline_complete"] is True
    stream_logs = [r for r in caplog.records if r.message.startswith("[LLM STREAM]")]
    assert len(stream_logs) == 1
    assert "streaming=True" in stream_logs[0].message


class _DeferredExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, job, on_done):
        self.jobs.append((job, on_done))


class _Conversation:
    def __init__(self):
        self.cancelled = []

    def prepare_turn(self, text, source, character_id):
        cancel = threading.Event()
        return PreparedTurn(
            lambda: {"reply": text},
            lambda: None,
            lambda: (cancel.set(), self.cancelled.append(text)),
        )


def test_barge_in_cancels_old_handler_completion_and_accepts_new_turn():
    events, executor, conversation = [], _DeferredExecutor(), _Conversation()
    event_bus = SimpleEventBus()
    event_bus.subscribe("EVT_CONVERSATION_TURN", events.append)
    handler = ConversationHandler(conversation, executor, event_bus)
    bus = ActionBus(event_bus)
    bus.register(handler)

    first = ActionCommand("conversation", "first", trace_id="trace-1", character_id="Choppr")
    second = ActionCommand("conversation", "second", trace_id="trace-2", character_id="Choppr")
    assert bus.execute(first).status == "ok"
    assert bus.cancel_conversation() is True
    assert bus.execute(second).status == "ok"

    executor.jobs[0][1](True, "", {"reply": "old"})
    executor.jobs[1][1](True, "", {"reply": "new"})

    assert conversation.cancelled == ["first"]
    assert [event.trace_id for event in events] == ["trace-2"]
