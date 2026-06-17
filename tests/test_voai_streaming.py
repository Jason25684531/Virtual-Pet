from __future__ import annotations

import io
import os
import sys
from pathlib import Path
import unittest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import api_client.voai_client as voai_client
from api_client.voai_client import VoAIStreamingTTSWorker, prewarm_voai_http_session


class _SignalCollector:
    def __init__(self):
        self.events = []

    def __call__(self, *args):
        self.events.append(args)


class _FakeResponse:
    def __init__(self, chunks=None, content=None, headers=None, error=None, status_code=200):
        self._chunks = list(chunks or [])
        self.content = content if content is not None else b"".join(self._chunks)
        self.headers = headers or {"content-type": "audio/wav"}
        self._error = error
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def iter_content(self, chunk_size=4096):
        del chunk_size
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


class _FakePcmPlayer:
    def __init__(self, available=True):
        self.available = available
        self.played = b""

    def is_available(self):
        return self.available

    def play_chunks(self, chunks, before_start=None):
        chunks = iter(chunks)
        try:
            first_chunk = next(chunks)
        except StopIteration:
            first_chunk = b""
        if first_chunk and callable(before_start):
            before_start()
        data = first_chunk + b"".join(chunks)
        self.played += data
        return len(data)


class _AudioReadyCollector:
    def __init__(self):
        self.events = []

    def __call__(self, audio_buffer: io.BytesIO, reply_id: str, trace_id: str):
        audio_buffer.seek(0)
        self.events.append((audio_buffer.read(), reply_id, trace_id))


class _PcmSinkCollector:
    def __init__(self):
        self.chunks = []
        self.finished_segments = []

    def enqueue_pcm_chunk(self, chunk: bytes, reply_id: str, trace_id: str):
        self.chunks.append((bytes(chunk), reply_id, trace_id))

    def finish_pcm_segment(self, reply_id: str, trace_id: str):
        self.finished_segments.append((reply_id, trace_id))


class VoAIStreamingTests(unittest.TestCase):
    def setUp(self):
        self._original_key = os.environ.get("VOAI_API_KEY")
        self._original_key_alt = os.environ.get("VoAI_API_KEY")
        self._original_streaming = os.environ.get("VOAI_PCM_STREAMING_ENABLED")
        os.environ.pop("VoAI_API_KEY", None)
        os.environ["VOAI_API_KEY"] = "test-key"
        os.environ["VOAI_PCM_STREAMING_ENABLED"] = "true"

    def tearDown(self):
        if self._original_key is None:
            os.environ.pop("VOAI_API_KEY", None)
        else:
            os.environ["VOAI_API_KEY"] = self._original_key
        if self._original_key_alt is None:
            os.environ.pop("VoAI_API_KEY", None)
        else:
            os.environ["VoAI_API_KEY"] = self._original_key_alt
        if self._original_streaming is None:
            os.environ.pop("VOAI_PCM_STREAMING_ENABLED", None)
        else:
            os.environ["VOAI_PCM_STREAMING_ENABLED"] = self._original_streaming

    def test_pcm_streaming_success_uses_pcm_headers_and_player(self):
        calls = []
        player = _FakePcmPlayer()
        progress = _SignalCollector()
        finished = _SignalCollector()

        def fake_post(*_args, **kwargs):
            calls.append(kwargs)
            return _FakeResponse(chunks=[b"pcm-a", b"pcm-b"], headers={"content-type": "audio/pcm"})

        worker = VoAIStreamingTTSWorker(
            text="測試",
            voice_id="miku",
            trace_id="trace-1",
            requests_post=fake_post,
            pcm_player_factory=lambda: player,
        )
        worker.progress_signal.connect(progress)
        worker.finished_signal.connect(finished)

        worker.run()

        self.assertEqual(player.played, b"pcm-apcm-b")
        self.assertEqual(calls[0]["headers"]["x-output-format"], "pcm")
        self.assertEqual(calls[0]["headers"]["x-sample-rate"], "32000")
        self.assertTrue(calls[0]["stream"])
        self.assertEqual(progress.events[0][0], "stream_started")
        self.assertEqual(progress.events[1][0], "driver_started")
        self.assertEqual(progress.events[2][0], "playback_started")
        self.assertTrue(finished.events[0][0])
        self.assertEqual(finished.events[0][2]["format"], "pcm")

    def test_deprecated_transport_mode_is_normalized_to_http_primary_path(self):
        player = _FakePcmPlayer()
        progress = _SignalCollector()
        finished = _SignalCollector()
        factory_calls = []
        calls = []

        def fake_transport_factory(**kwargs):
            factory_calls.append(kwargs)
            return None

        def fake_post(*_args, **kwargs):
            calls.append(kwargs)
            return _FakeResponse(chunks=[b"pcm-a", b"pcm-b"], headers={"content-type": "audio/pcm"})

        worker = VoAIStreamingTTSWorker(
            text="測試 websocket shim",
            trace_id="trace-http-only",
            requests_post=fake_post,
            pcm_player_factory=lambda: player,
            transport_mode="websocket",
            transport_session_factory=fake_transport_factory,
        )
        worker.progress_signal.connect(progress)
        worker.finished_signal.connect(finished)

        worker.run()

        self.assertEqual(player.played, b"pcm-apcm-b")
        self.assertEqual(factory_calls, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["headers"]["x-output-format"], "pcm")
        self.assertEqual(finished.events[0][2]["transport"], "http")
        self.assertFalse(any(event[0] in {"transport_selected", "transport_fallback"} for event in progress.events))

    def test_prewarm_uses_authenticated_shared_session_request(self):
        calls = []
        original_get = voai_client._VOAI_HTTP_SESSION.get

        def fake_get(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeResponse(headers={"content-type": "application/json"}, status_code=200)

        voai_client._VOAI_HTTP_SESSION.get = fake_get
        try:
            payload = prewarm_voai_http_session(trace_id="trace-prewarm")
        finally:
            voai_client._VOAI_HTTP_SESSION.get = original_get

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["advisory"])
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(calls[0][0][0], voai_client._VOAI_PREWARM_URL)
        self.assertEqual(calls[0][1]["headers"]["x-api-key"], "test-key")

    def test_prewarm_failure_is_advisory_only(self):
        def fake_get(*_args, **_kwargs):
            raise requests.ConnectionError("prewarm failed")

        payload = prewarm_voai_http_session(trace_id="trace-prewarm-failed", requests_get=fake_get)

        self.assertFalse(payload["ok"])
        self.assertTrue(payload["advisory"])
        self.assertEqual(payload["failure_code"], "request_error")
        self.assertNotIn("fast_fail", payload)

    def test_pcm_unavailable_falls_back_to_mp3_audio_ready(self):
        calls = []
        audio_ready = _AudioReadyCollector()
        finished = _SignalCollector()

        def fake_post(*_args, **kwargs):
            calls.append(kwargs)
            return _FakeResponse(content=b"mp3-bytes", headers={"content-type": "audio/mpeg"})

        worker = VoAIStreamingTTSWorker(
            text="測試",
            trace_id="trace-2",
            requests_post=fake_post,
            pcm_player_factory=lambda: _FakePcmPlayer(available=False),
        )
        worker.audio_ready_signal.connect(audio_ready)
        worker.finished_signal.connect(finished)

        worker.run()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["headers"]["x-output-format"], "mp3")
        self.assertEqual(audio_ready.events[0][0], b"mp3-bytes")
        self.assertTrue(finished.events[0][0])
        self.assertEqual(finished.events[0][2]["format"], "mp3")

    def test_pcm_streaming_can_handoff_to_pcm_sink_session(self):
        calls = []
        sink = _PcmSinkCollector()
        finished = _SignalCollector()

        def fake_post(*_args, **kwargs):
            calls.append(kwargs)
            return _FakeResponse(chunks=[b"pcm-a", b"pcm-b"], headers={"content-type": "audio/pcm"})

        worker = VoAIStreamingTTSWorker(
            text="測試 sink",
            trace_id="trace-sink",
            reply_id="reply-sink",
            requests_post=fake_post,
            pcm_player_factory=lambda: _FakePcmPlayer(),
            pcm_stream_sink=sink,
            transport_mode="http",
        )
        worker.finished_signal.connect(finished)

        worker.run()

        self.assertEqual(len(calls), 1)
        self.assertEqual(sink.chunks, [(b"pcm-a", "reply-sink", "trace-sink"), (b"pcm-b", "reply-sink", "trace-sink")])
        self.assertEqual(sink.finished_segments, [("reply-sink", "trace-sink")])
        self.assertTrue(finished.events[0][0])
        self.assertTrue(finished.events[0][2]["queued_playback"])
        self.assertTrue(finished.events[0][2]["pcm_stream_session"])

    def test_missing_api_key_emits_safe_failure(self):
        os.environ.pop("VOAI_API_KEY", None)
        os.environ.pop("VoAI_API_KEY", None)
        finished = _SignalCollector()
        worker = VoAIStreamingTTSWorker(text="測試")
        worker.finished_signal.connect(finished)

        worker.run()

        self.assertFalse(finished.events[0][0])
        self.assertIn("缺少 VOAI_API_KEY", finished.events[0][1])

    def test_http_529_emits_structured_fast_fail_when_adaptive_enabled(self):
        finished = _SignalCollector()

        class _FakeHttpError(requests.HTTPError):
            def __init__(self):
                super().__init__("529 Server Busy")
                self.response = type("Resp", (), {"status_code": 529, "text": "busy"})()

        def fake_post(*_args, **_kwargs):
            return _FakeResponse(error=_FakeHttpError())

        worker = VoAIStreamingTTSWorker(
            text="測試",
            trace_id="trace-fast-fail",
            requests_post=fake_post,
            pcm_player_factory=lambda: _FakePcmPlayer(),
            adaptive_fallback_enabled=True,
        )
        worker.finished_signal.connect(finished)

        worker.run()

        self.assertFalse(finished.events[0][0])
        payload = finished.events[0][2]
        self.assertTrue(payload["fast_fail"])
        self.assertEqual(payload["failure_code"], "http_529")
        self.assertEqual(payload["provider"], "voai")


if __name__ == "__main__":
    unittest.main()
