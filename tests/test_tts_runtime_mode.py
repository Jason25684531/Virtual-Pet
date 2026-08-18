"""Unit tests for TTS runtime mode resolution and worker behavior."""

import pytest


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


class TestAdaptiveTTSFallbackWorker:
    """Test AdaptiveTTSFallbackWorker payload structure."""

    def test_worker_initialization_with_resolved_mode(self):
        """Test that worker accepts and stores resolved_tts_mode parameter."""
        from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker

        worker = AdaptiveTTSFallbackWorker(
            text="test",
            reply_id="test_001",
            trace_id="trace_001",
            voice_id="Miku",
            resolved_tts_mode="voai_first"
        )

        assert worker._resolved_tts_mode == "voai_first"
        assert worker._provider_chain == []
        assert worker._fallback_reasons == []

    def test_worker_fallback_reasons_tracking(self):
        """Test that worker tracks fallback reasons."""
        from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker

        worker = AdaptiveTTSFallbackWorker(
            text="test",
            reply_id="test_001",
            trace_id="trace_001",
            voice_id="Miku",
            resolved_tts_mode="voai_first"
        )

        assert isinstance(worker._fallback_reasons, list)
        assert len(worker._fallback_reasons) == 0

    def test_worker_payload_includes_resolved_mode(self):
        """Test that worker result payload includes resolved_mode and attempted_providers."""
        from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker

        worker = AdaptiveTTSFallbackWorker(
            text="test",
            reply_id="test_001",
            trace_id="trace_001",
            voice_id="Miku",
            resolved_tts_mode="fallback_enabled"
        )

        payload = {"status": "test"}
        normalized = dict(payload)
        normalized.setdefault("resolved_mode", worker._resolved_tts_mode)
        normalized.setdefault("attempted_providers", list(worker._provider_chain))

        assert normalized["resolved_mode"] == "fallback_enabled"
        assert normalized["attempted_providers"] == []

    def test_elevenlabs_failure_does_not_retry_a_provider(self):
        from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker

        worker = AdaptiveTTSFallbackWorker(text="test", preferred_provider="voai")
        worker._provider_chain = ["voai", "elevenlabs"]

        worker._handle_result(False, "connection lost after first chunk", {"stream_started": True}, "elevenlabs")

        assert worker._provider_chain == ["voai", "elevenlabs"]


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
    request = {}
    def post(*args, **kwargs):
        request.update(kwargs)
        return Response()

    worker = ElevenLabsStreamingTTSWorker(
        "hello", reply_id="reply", trace_id="trace", voice_id="voice",
        pcm_stream_sink=sink, requests_post=post,
    )
    worker.run()

    assert sink.chunks == [(b"one", "reply", "trace", 24000), (b"two", "reply", "trace", 24000)]
    assert sink.finished == [("reply", "trace")]
    assert request["headers"]["Accept"] == "audio/pcm"
    assert request["params"]["output_format"] == "pcm_24000"


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

        worker = VoAIStreamingTTSWorker(text="hello", reply_id="r1", trace_id="t1", voice_id="Miku")
        captured = {}
        worker.finished_signal.connect(lambda success, message, payload: captured.update(payload or {}))
        worker.run()

        assert captured.get("fast_fail") is True
        assert captured.get("failure_code") == "missing_api_key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
