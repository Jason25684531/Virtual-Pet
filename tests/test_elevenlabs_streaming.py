from __future__ import annotations

import io
import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker


class _SignalCollector:
    def __init__(self):
        self.events: list[tuple[bool, str, object]] = []

    def __call__(self, success: bool, message: str, payload: object):
        self.events.append((success, message, payload))


class _ProgressCollector:
    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def __call__(self, event_name: str, payload: object):
        self.events.append((event_name, payload))


class FakeResponse:
    def __init__(self, chunks=None, headers=None, error: Exception | None = None):
        self._chunks = list(chunks or [])
        self.headers = headers or {"content-type": "audio/mpeg"}
        self._error = error
        self.closed = False

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def iter_content(self, chunk_size: int = 4096):
        del chunk_size
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


class FakeStdin(io.BytesIO):
    def close(self):
        self.was_closed = True
        super().close()


class FakePopen:
    def __init__(self, *_args, **_kwargs):
        self.stdin = FakeStdin()
        self.stderr = io.BytesIO()
        self.killed = False
        self.wait_called = False
        self._return_code = 0

    def wait(self, timeout: int | None = None):
        del timeout
        self.wait_called = True
        return self._return_code

    def poll(self):
        return self._return_code if self.wait_called else None

    def kill(self):
        self.killed = True


class ElevenLabsStreamingWorkerTests(unittest.TestCase):
    def test_missing_ffplay_emits_safe_fallback(self):
        collector = _SignalCollector()
        worker = ElevenLabsStreamingTTSWorker(
            text="測試語音",
            voice_id="voice",
            which_resolver=lambda _name: None,
        )
        worker.finished_signal.connect(collector)

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("找不到 ffplay", collector.events[0][1])

    def test_streaming_success_forwards_audio_bytes_without_temp_file(self):
        collector = _SignalCollector()
        progress = _ProgressCollector()
        popen = FakePopen()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[b"abc", b"def"])

        worker = ElevenLabsStreamingTTSWorker(
            text="測試串流播放",
            voice_id="voice",
            trace_id="trace-1234",
            requests_post=fake_post,
            popen_factory=lambda *_args, **_kwargs: popen,
            which_resolver=lambda _name: "/usr/bin/ffplay",
        )
        worker.finished_signal.connect(collector)
        worker.progress_signal.connect(progress)

        original_environ = dict()
        for key in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
            original_environ[key] = __import__("os").environ.get(key)
        __import__("os").environ["ELEVENLABS_API_KEY"] = "test-key"

        try:
            worker.run()
        finally:
            import os

            for key, value in original_environ.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(len(collector.events), 1)
        success, message, payload = collector.events[0]
        self.assertTrue(success)
        self.assertIn("播放完成", message)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["bytes_forwarded"], 6)
        self.assertEqual(payload["trace_id"], "trace-1234")
        self.assertTrue(popen.wait_called)
        self.assertEqual(progress.events[0][0], "stream_started")
        self.assertEqual(progress.events[0][1]["trace_id"], "trace-1234")

    def test_invalid_audio_payload_emits_warning(self):
        collector = _SignalCollector()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[b"not-audio"], headers={"content-type": "application/json"})

        worker = ElevenLabsStreamingTTSWorker(
            text="測試",
            voice_id="voice",
            requests_post=fake_post,
            popen_factory=lambda *_args, **_kwargs: FakePopen(),
            which_resolver=lambda _name: "/usr/bin/ffplay",
        )
        worker.finished_signal.connect(collector)

        import os

        original_api_key = os.environ.get("ELEVENLABS_API_KEY")
        os.environ["ELEVENLABS_API_KEY"] = "test-key"
        try:
            worker.run()
        finally:
            if original_api_key is None:
                os.environ.pop("ELEVENLABS_API_KEY", None)
            else:
                os.environ["ELEVENLABS_API_KEY"] = original_api_key

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("無效音訊格式", collector.events[0][1])


if __name__ == "__main__":
    unittest.main()
