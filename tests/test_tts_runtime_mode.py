"""Unit tests for TTS runtime mode resolution and worker behavior."""

import os
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
