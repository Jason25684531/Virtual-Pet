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

from api_client.voai_client import VoAIStreamingTTSWorker


class _SignalCollector:
    def __init__(self):
        self.events = []

    def __call__(self, *args):
        self.events.append(args)


class _FakeResponse:
    def __init__(self, chunks=None, content=None, headers=None, error=None):
        self._chunks = list(chunks or [])
        self.content = content if content is not None else b"".join(self._chunks)
        self.headers = headers or {"content-type": "audio/wav"}
        self._error = error
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


class VoAIStreamingTests(unittest.TestCase):
    def setUp(self):
        self._original_key = os.environ.get("VOAI_API_KEY")
        self._original_key_alt = os.environ.get("VoAI_API_KEY")
        self._original_streaming = os.environ.get("VOAI_PCM_STREAMING_ENABLED")
        os.environ["VOAI_API_KEY"] = "test-key"
        os.environ.pop("VoAI_API_KEY", None)
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
