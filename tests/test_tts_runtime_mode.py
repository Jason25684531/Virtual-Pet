"""Unit tests for TTS runtime mode resolution and worker behavior."""

import dataclasses

import pytest
from PyQt5.QtCore import QObject, pyqtSignal

from api_client.tts_contract import TtsRequest, build_tts_provider_chain


class TestTTSRuntimeMode:
    """Test TTS runtime mode resolution."""

    def test_resolve_tts_runtime_mode_with_voai_key(self, monkeypatch):
        """Test that VOAI-first mode is selected when VOAI_API_KEY is available."""
        import config

        monkeypatch.setenv("VOAI_API_KEY", "test_voai_key_12345")
        resolved_mode, reason = config.resolve_tts_runtime_mode()

        assert resolved_mode == "voai_first"
        assert reason == "voai_api_key_available"

    def test_resolve_tts_runtime_mode_without_voai_key(self, monkeypatch):
        """Test that fallback_enabled mode is selected when VOAI_API_KEY is missing."""
        import config

        monkeypatch.delenv("VOAI_API_KEY", raising=False)
        monkeypatch.delenv("VoAI_API_KEY", raising=False)
        resolved_mode, reason = config.resolve_tts_runtime_mode()

        assert resolved_mode == "fallback_enabled"
        assert reason == "voai_api_key_missing"

    def test_get_voai_api_key(self, monkeypatch):
        """Test VOAI API key retrieval."""
        import config

        test_key = "iq-eTXOITXAdn03NE2nJ4b1xDswFPjq8TtP7joZIY7qAEI="
        monkeypatch.setenv("VOAI_API_KEY", test_key)
        result = config.get_voai_api_key()

        assert result == test_key

    def test_get_voai_api_key_fallback(self, monkeypatch):
        """Test VOAI API key fallback to VoAI_API_KEY."""
        import config

        monkeypatch.delenv("VOAI_API_KEY", raising=False)
        test_key = "voai_fallback_key"
        monkeypatch.setenv("VoAI_API_KEY", test_key)
        result = config.get_voai_api_key()

        assert result == test_key

    def test_get_voai_api_key_empty(self, monkeypatch):
        """Test empty VOAI API key."""
        import config

        monkeypatch.delenv("VOAI_API_KEY", raising=False)
        monkeypatch.delenv("VoAI_API_KEY", raising=False)
        result = config.get_voai_api_key()

        assert result == ""


class _StubStreamingWorker(QObject):
    """只實作 StreamingTTSWorker 契約,不做真正的網路呼叫;測試以
    `_handle_result` 直接驅動 AdaptiveTTSFallbackWorker 的備援決策。"""

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)
    finished = pyqtSignal()

    def __init__(self, request: TtsRequest, parent=None):
        super().__init__(parent)

    def start(self):
        pass

    def isRunning(self):
        return False

    def quit(self):
        pass

    def wait(self, timeout_ms: int = 5000) -> bool:
        return True


def _stub_chain(character_id: str, preferred_provider: str | None = None):
    """沿用 build_tts_provider_chain 真正的順序與備援判斷規則,只把
    factory 換成不打真網路的 stub,方便測試備援決策而不需要 mock HTTP。"""
    return tuple(
        dataclasses.replace(provider, factory=_StubStreamingWorker)
        for provider in build_tts_provider_chain(character_id, preferred_provider)
    )


def _make_worker(character_id="char-RO", preferred_provider=None, resolved_tts_mode="voai_first"):
    from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker

    chain = _stub_chain(character_id, preferred_provider)
    request = TtsRequest(
        text="test", reply_id="test_001", trace_id="trace_001",
        character_id=character_id, voice_id="v", model_id="m",
        preferred_provider=preferred_provider or "", resolved_tts_mode=resolved_tts_mode,
    )
    return AdaptiveTTSFallbackWorker(request, chain=chain)


class TestAdaptiveTTSFallbackWorker:
    """Test AdaptiveTTSFallbackWorker payload structure and fallback decisions."""

    def test_worker_initialization_starts_with_empty_chain_state(self):
        worker = _make_worker(resolved_tts_mode="voai_first")

        assert worker._request.resolved_tts_mode == "voai_first"
        assert worker._provider_chain == []
        assert worker._fallback_reasons == []

    def test_worker_without_explicit_chain_defaults_to_build_tts_provider_chain(self):
        from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker

        request = TtsRequest(
            text="test", reply_id="r1", trace_id="t1", character_id="char-Adol",
            voice_id="v", model_id="m",
        )
        worker = AdaptiveTTSFallbackWorker(request)

        assert [p.name for p in worker._chain] == [p.name for p in build_tts_provider_chain("char-Adol")]

    def test_worker_payload_includes_resolved_mode_and_attempted_providers(self):
        worker = _make_worker(character_id="char-RO", resolved_tts_mode="fallback_enabled")
        results = []
        worker.finished_signal.connect(lambda success, message, payload: results.append(payload))
        worker._running = True
        worker._chain_index = 0
        worker._provider_chain = ["voai"]

        worker._handle_result(True, "ok", {}, "voai")

        assert results[0]["resolved_mode"] == "fallback_enabled"
        assert results[0]["attempted_providers"] == ["voai"]

    def test_dedicated_voice_character_prefers_elevenlabs(self):
        import config

        assert "char-Adol" in config.BUILTIN_CHARACTER_ELEVENLABS_VOICE_IDS
        assert _make_worker("char-Adol")._chain[0].name == "elevenlabs"

        # 無專屬聲線的角色維持 VoAI 優先；顯式指定時以指定值為準。
        assert _make_worker("char-RO")._chain[0].name == "voai"
        assert _make_worker("char-Adol", preferred_provider="voai")._chain[0].name == "voai"

    def test_elevenlabs_initial_failure_falls_back_to_voai(self):
        """char-Adol 有專屬 ElevenLabs 聲線,首選 elevenlabs;失敗一律換 voai
        (ElevenLabs 不像 VoAI 有 fast_fail 門檻,任何失敗都無條件換下一個)。"""
        worker = _make_worker("char-Adol")
        assert [p.name for p in worker._chain] == ["elevenlabs", "voai"]
        advanced = []
        worker._advance = lambda *, reason: advanced.append(reason)
        worker._chain_index = 0
        worker._provider_chain = ["elevenlabs"]

        worker._handle_result(False, "quota exceeded", {}, "elevenlabs")

        assert advanced == ["quota exceeded"]
        assert worker._fallback_reasons == [("elevenlabs", "quota exceeded")]

    def test_voai_failure_without_fast_fail_does_not_try_elevenlabs(self):
        """VoAI 只在 fast_fail(缺 key、4xx/5xx、連線層錯誤)時才換下一個供應商;
        串流中途的非定性錯誤視為終局失敗,不嘗試 ElevenLabs。"""
        worker = _make_worker("char-RO")
        assert [p.name for p in worker._chain] == ["voai", "elevenlabs"]
        advanced = []
        worker._advance = lambda *, reason: advanced.append(reason)
        worker._running = True
        worker._chain_index = 0
        worker._provider_chain = ["voai"]
        results = []
        worker.finished_signal.connect(lambda success, message, payload: results.append(payload))

        worker._handle_result(False, "connection lost after first chunk", {}, "voai")

        assert advanced == []
        assert results[0]["outcome"] == "provider_failed"
        assert "critical_tts_failure" not in results[0]

    def test_failure_at_end_of_chain_marks_critical_failure_and_does_not_advance_again(self):
        worker = _make_worker("char-RO")
        advanced = []
        worker._advance = lambda *, reason: advanced.append(reason)
        worker._running = True
        worker._chain_index = 1
        worker._provider_chain = ["voai", "elevenlabs"]
        results = []
        worker.finished_signal.connect(lambda success, message, payload: results.append(payload))

        worker._handle_result(False, "connection lost after first chunk", {"stream_started": True}, "elevenlabs")

        assert advanced == []
        assert worker._provider_chain == ["voai", "elevenlabs"]
        assert results[0]["critical_tts_failure"] is True
        assert results[0]["outcome"] == "all_providers_failed"


def test_elevenlabs_pcm_chunks_are_handed_to_the_pcm_sink(monkeypatch):
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    class Response:
        headers = {"content-type": "audio/pcm"}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): return iter([b"one", b"two"])
        def close(self): pass

    class Sink:
        def __init__(self): self.chunks = []; self.finished = []
        def enqueue_pcm_chunk(self, chunk, reply_id, trace_id, sample_rate=None):
            self.chunks.append((chunk, reply_id, trace_id, sample_rate))
        def finish_pcm_segment(self, reply_id, trace_id): self.finished.append((reply_id, trace_id))

    monkeypatch.setenv("ELEVENLABS_API_KEY", "key")
    sink = Sink()
    captured_request = {}
    def post(*args, **kwargs):
        captured_request.update(kwargs)
        return Response()

    request = TtsRequest(
        text="hello", reply_id="reply", trace_id="trace", character_id="voice",
        voice_id="voice", model_id="", pcm_stream_sink=sink,
    )
    worker = ElevenLabsStreamingTTSWorker(request, requests_post=post)
    worker.run()

    assert sink.chunks == [(b"one", "reply", "trace", 24000), (b"two", "reply", "trace", 24000)]
    assert sink.finished == [("reply", "trace")]
    assert captured_request["headers"]["Accept"] == "audio/pcm"
    assert captured_request["params"]["output_format"] == "pcm_24000"


class TestVoAIFastFailClassification:
    """VoAI 失敗時必須標記 fast_fail，才能 cascade 到 ElevenLabs（api provider）。"""

    def test_http_error_status_is_definitive(self):
        from api_client.voai_client import _classify_fast_fail

        class _Resp:
            status_code = 401

        class _Exc(Exception):
            response = _Resp()

        _, _, definitive = _classify_fast_fail(_Exc("unauthorized"))
        assert definitive is True

    def test_http_529_is_definitive(self):
        from api_client.voai_client import _classify_fast_fail

        class _Resp:
            status_code = 529

        class _Exc(Exception):
            response = _Resp()

        _, _, definitive = _classify_fast_fail(_Exc("busy"))
        assert definitive is True

    def test_connection_error_is_definitive(self):
        import requests
        from api_client.voai_client import _classify_fast_fail

        _, _, definitive = _classify_fast_fail(requests.ConnectionError("down"))
        assert definitive is True

    def test_missing_api_key_marks_fast_fail(self, monkeypatch):
        from api_client.voai_client import VoAIStreamingTTSWorker

        monkeypatch.delenv("VOAI_API_KEY", raising=False)
        monkeypatch.delenv("VoAI_API_KEY", raising=False)

        request = TtsRequest(
            text="hello", reply_id="r1", trace_id="t1", character_id="Miku",
            voice_id="", model_id="",
        )
        worker = VoAIStreamingTTSWorker(request)
        captured = {}
        worker.finished_signal.connect(lambda success, message, payload: captured.update(payload or {}))
        worker.run()

        assert captured.get("fast_fail") is True
        assert captured.get("failure_code") == "missing_api_key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
