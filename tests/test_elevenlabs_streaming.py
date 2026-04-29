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


class FakeAudioPlayer:
    def __init__(self):
        self.played_bytes: list[bytes] = []

    def play(self, audio_buffer: io.BytesIO):
        self.played_bytes.append(audio_buffer.read())


class RaisingAudioPlayer:
    def play(self, _audio_buffer: io.BytesIO):
        raise RuntimeError("audio backend unavailable")


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
        collector = _SignalCollector()
        os.environ.pop("ELEVENLABS_API_KEY", None)

        worker = ElevenLabsStreamingTTSWorker(
            text="測試語音",
            voice_id="voice",
            audio_player=FakeAudioPlayer(),
        )
        worker.finished_signal.connect(collector)

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("缺少 ElevenLabs API Key", collector.events[0][1])

    def test_streaming_success_buffers_audio_in_memory_and_plays_it(self):
        collector = _SignalCollector()
        progress = _ProgressCollector()
        player = FakeAudioPlayer()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[b"abc", b"def"])

        worker = ElevenLabsStreamingTTSWorker(
            text="測試串流播放",
            voice_id="voice",
            trace_id="trace-1234",
            requests_post=fake_post,
            audio_player=player,
        )
        worker.finished_signal.connect(collector)
        worker.progress_signal.connect(progress)
        os.environ["ELEVENLABS_API_KEY"] = "test-key"

        worker.run()

        self.assertEqual(len(collector.events), 1)
        success, message, payload = collector.events[0]
        self.assertTrue(success)
        self.assertIn("播放完成", message)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["bytes_forwarded"], 6)
        self.assertEqual(payload["trace_id"], "trace-1234")
        self.assertEqual(player.played_bytes, [b"abcdef"])
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
            audio_player=FakeAudioPlayer(),
        )
        worker.finished_signal.connect(collector)
        os.environ["ELEVENLABS_API_KEY"] = "test-key"

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("無效音訊格式", collector.events[0][1])

    def test_audio_player_failure_emits_warning(self):
        collector = _SignalCollector()

        def fake_post(*_args, **_kwargs):
            return FakeResponse(chunks=[b"abc"])

        worker = ElevenLabsStreamingTTSWorker(
            text="測試",
            voice_id="voice",
            requests_post=fake_post,
            audio_player=RaisingAudioPlayer(),
        )
        worker.finished_signal.connect(collector)
        os.environ["ELEVENLABS_API_KEY"] = "test-key"

        worker.run()

        self.assertEqual(len(collector.events), 1)
        self.assertFalse(collector.events[0][0])
        self.assertIn("audio backend unavailable", collector.events[0][1])

    def test_pygame_audio_player_loads_mp3_from_memory(self):
        mixer = _FakeMixer()
        player = PygameInMemoryAudioPlayer(mixer_module=mixer, poll_interval=0)

        player.play(io.BytesIO(b"fake-mp3-bytes"))

        self.assertEqual(mixer.init_calls, 1)
        self.assertEqual(mixer.music.loaded_bytes, b"fake-mp3-bytes")
        self.assertEqual(mixer.music.namehint, "mp3")
        self.assertEqual(mixer.music.play_calls, 1)


if __name__ == "__main__":
    unittest.main()
