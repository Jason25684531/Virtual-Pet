from __future__ import annotations

import io
import os
import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker, PygameInMemoryAudioPlayer


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


class _AudioReadyCollector:
    """收集 audio_ready_signal(BytesIO, reply_id, trace_id) 的 emit。"""
    def __init__(self):
        self.events: list[tuple[bytes, str, str]] = []

    def __call__(self, audio_buffer: io.BytesIO, reply_id: str, trace_id: str):
        audio_buffer.seek(0)
        self.events.append((audio_buffer.read(), reply_id, trace_id))


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


class _FakeMusic:
    def __init__(self):
        self.loaded_bytes = b""
        self.play_calls = 0
        self._busy_reads = 0

    def stop(self):
        return None

    def unload(self):
        return None

    def load(self, audio_buffer: io.BytesIO, namehint: str | None = None):
        self.loaded_bytes = audio_buffer.read()
        self.namehint = namehint

    def play(self):
        self.play_calls += 1
        self._busy_reads = 0

    def get_busy(self):
        self._busy_reads += 1
        return self._busy_reads == 1


class _FakeMixer:
    def __init__(self):
        self.music = _FakeMusic()
        self.init_calls = 0
        self._initialized = False

    def get_init(self):
        return self._initialized

    def init(self, **_kwargs):
        self._initialized = True
        self.init_calls += 1


class ElevenLabsStreamingWorkerTests(unittest.TestCase):
    def setUp(self):
        self._original_api_key = os.environ.get("ELEVENLABS_API_KEY")
        self._original_voice_id = os.environ.get("ELEVENLABS_VOICE_ID")

    def tearDown(self):
        if self._original_api_key is None:
            os.environ.pop("ELEVENLABS_API_KEY", None)
        else:
            os.environ["ELEVENLABS_API_KEY"] = self._original_api_key
        if self._original_voice_id is None:
            os.environ.pop("ELEVENLABS_VOICE_ID", None)
        else:
            os.environ["ELEVENLABS_VOICE_ID"] = self._original_voice_id

    def test_missing_credentials_emit_safe_fallback(self):
        """缺少 API Key 時應 emit finished_signal(False, ...)。"""
        collector = _SignalCollector()
        os.environ.pop("ELEVENLABS_API_KEY", None)

        worker = ElevenLabsStreamingTTSWorker(
            text="測試語音",
            voice_id="voice",
        )
        worker.finished_signal.connect(collector)

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("缺少 ElevenLabs API Key", collector.events[0][1])

    def test_streaming_success_emits_audio_ready_signal(self):
        """串流成功時應 emit audio_ready_signal(BytesIO, reply_id, trace_id)。"""
        finish_collector = _SignalCollector()
        progress_collector = _ProgressCollector()
        audio_collector = _AudioReadyCollector()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[b"abc", b"def"])

        worker = ElevenLabsStreamingTTSWorker(
            text="測試串流播放",
            voice_id="voice",
            trace_id="trace-1234",
            requests_post=fake_post,
        )
        worker.finished_signal.connect(finish_collector)
        worker.progress_signal.connect(progress_collector)
        worker.audio_ready_signal.connect(audio_collector)
        os.environ["ELEVENLABS_API_KEY"] = "test-key"

        worker.run()

        # audio_ready_signal 應被 emit 一次，包含完整音訊 bytes
        self.assertEqual(len(audio_collector.events), 1)
        audio_bytes, reply_id, trace_id = audio_collector.events[0]
        self.assertEqual(audio_bytes, b"abcdef")
        self.assertEqual(trace_id, "trace-1234")

        # finished_signal 應 emit 成功
        self.assertEqual(len(finish_collector.events), 1)
        success, message, payload = finish_collector.events[0]
        self.assertTrue(success)
        self.assertIn("已送入播放佇列", message)
        self.assertEqual(payload["bytes_forwarded"], 6)
        self.assertEqual(payload["trace_id"], "trace-1234")

        # progress_signal stream_started 應被 emit
        self.assertEqual(progress_collector.events[0][0], "stream_started")
        self.assertEqual(progress_collector.events[0][1]["trace_id"], "trace-1234")

    def test_invalid_audio_payload_emits_warning(self):
        """回傳非 audio content-type 時應 emit finished_signal(False, ...)。"""
        collector = _SignalCollector()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[b"not-audio"], headers={"content-type": "application/json"})

        worker = ElevenLabsStreamingTTSWorker(
            text="測試",
            voice_id="voice",
            requests_post=fake_post,
        )
        worker.finished_signal.connect(collector)
        os.environ["ELEVENLABS_API_KEY"] = "test-key"

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("無效音訊格式", collector.events[0][1])

    def test_empty_audio_response_emits_warning(self):
        """收到空音訊 bytes 時應 emit finished_signal(False, ...)。"""
        collector = _SignalCollector()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[])  # 沒有任何 chunk

        worker = ElevenLabsStreamingTTSWorker(
            text="測試",
            voice_id="voice",
            requests_post=fake_post,
        )
        worker.finished_signal.connect(collector)
        os.environ["ELEVENLABS_API_KEY"] = "test-key"

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])

    def test_pygame_audio_player_loads_mp3_from_memory(self):
        """PygameInMemoryAudioPlayer 應正確透過 mixer.music 播放 BytesIO。"""
        mixer = _FakeMixer()
        player = PygameInMemoryAudioPlayer(mixer_module=mixer, poll_interval=0)

        player.play(io.BytesIO(b"fake-mp3-bytes"))

        self.assertEqual(mixer.init_calls, 1)
        self.assertEqual(mixer.music.loaded_bytes, b"fake-mp3-bytes")
        self.assertEqual(mixer.music.namehint, "mp3")
        self.assertEqual(mixer.music.play_calls, 1)


if __name__ == "__main__":
    unittest.main()
