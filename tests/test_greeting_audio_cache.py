"""reduce-turn-latency 3.x:打招呼音訊快取(模組單元 + _start_next_tts_worker 整合)。"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest
from PyQt5.QtCore import QObject

from api_client import greeting_audio_cache as gac
from api_client.tts_contract import TtsRequest
from tts_playback import TtsPlaybackMixin

PCM = b"\x01\x00" * 5000  # 10000 bytes,2 個 4096 切片以上


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gac, "CACHE_DIR", tmp_path / "greetings")


def _req(voice="v1", trace="greeting-1", text="今天過得好嗎?", sink=None):
    return TtsRequest(text=text, reply_id="r1", trace_id=trace, character_id="char-A",
                      voice_id=voice, model_id="m", pcm_stream_sink=sink)


def test_store_load_roundtrip_and_key_changes_with_voice():
    gac.store(_req(), PCM, 24000)
    assert gac.load(_req()) == (PCM, 24000)
    assert gac.load(_req(voice="v2")) is None


def test_corrupt_file_is_deleted_and_misses():
    gac.store(_req(), PCM, 24000)
    path = next(gac.CACHE_DIR.glob("*.pcm"))
    path.write_bytes(b"\x01\x02\x03")  # 奇數長度
    assert gac.load(_req()) is None
    assert not path.exists()


def test_cached_worker_replays_in_order_with_sample_rate():
    sink, results = MagicMock(), []
    worker = gac.CachedGreetingWorker(_req(sink=sink), (PCM, 24000))
    worker.finished_signal.connect(lambda ok, msg, payload: results.append((ok, payload)))
    worker.run()  # 同步執行 run,不開執行緒
    sent = b"".join(c.args[0] for c in sink.enqueue_pcm_chunk.call_args_list)
    assert sent == PCM
    assert all(c.kwargs["sample_rate"] == 24000 for c in sink.enqueue_pcm_chunk.call_args_list)
    sink.finish_pcm_segment.assert_called_once_with("r1", "greeting-1")
    assert results[0][0] is True and results[0][1]["queued_playback"] is True


def test_recording_sink_forwards_unchanged_and_stores_only_when_clean():
    inner = MagicMock()
    ok = {"format": "pcm", "attempted_providers": ["elevenlabs"]}
    cases = [
        (True, ok, False, True),
        (False, ok, False, False),  # 失敗
        (True, ok, True, False),  # 被抑制/打斷
        (True, {"format": "pcm", "attempted_providers": ["elevenlabs", "voai"]}, False, False),  # fallback
        (True, {"format": "mp3", "attempted_providers": ["voai"]}, False, False),
    ]
    for success, payload, suppressed, expect_cached in cases:
        for f in gac.CACHE_DIR.glob("*") if gac.CACHE_DIR.exists() else []:
            f.unlink()
        rec = gac.RecordingSink(inner)
        rec.enqueue_pcm_chunk(PCM, "r1", "greeting-1", sample_rate=24000)
        rec.finish_pcm_segment("r1", "greeting-1")
        rec.store_if_clean(_req(), success, payload, suppressed)
        assert (gac.load(_req()) is not None) is expect_cached, (success, payload, suppressed)
    inner.enqueue_pcm_chunk.assert_called_with(PCM, "r1", "greeting-1", sample_rate=24000)


class _Coordinator(TtsPlaybackMixin, QObject):
    def __init__(self, factory):
        super().__init__()
        self._tts_worker_factory = factory
        self._active_tts_worker = None
        self._pending_tts_chunks = queue.Queue()
        self._suppressed_traces = {}
        self._tts_not_expected_traces = set()
        self._trace_tts_providers = {}
        self._can_start_trace_audio = lambda *_a, **_k: True
        self._audio_worker = MagicMock()
        self._workers = []
        self.finished_calls = []

    def _current_character_id(self):
        return "char-A"

    def _on_tts_finished(self, *a, **_k):
        self.finished_calls.append(a)

    _on_tts_progress = _record_spoken_reply = _finish_loop_action_if_tts_idle = lambda self, *a, **k: None


class _FakeWorker(QObject):
    """只收集建構時的 request,測試手動觸發 finished_signal。"""

    from PyQt5.QtCore import pyqtSignal
    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)
    finished = pyqtSignal()

    def start(self):
        pass


def _run(coordinator, trace="greeting-1"):
    coordinator._pending_tts_chunks.put(("r1", "今天過得好嗎?", trace))
    coordinator._start_next_tts_worker()


def test_greeting_miss_then_hit_skips_provider_factory():
    made = []

    def factory(request, parent=None):
        made.append(request)
        return _FakeWorker()

    c = _Coordinator(factory)
    _run(c)
    assert len(made) == 1 and isinstance(made[0].pcm_stream_sink, gac.RecordingSink)
    made[0].pcm_stream_sink.enqueue_pcm_chunk(PCM, "r1", "greeting-1", sample_rate=24000)
    c._active_tts_worker.finished_signal.emit(True, "ok", {"format": "pcm", "attempted_providers": ["elevenlabs"]})
    assert gac.load(made[0]) is not None

    c2 = _Coordinator(factory)
    _run(c2)
    assert len(made) == 1  # 第二次命中,沒再建 provider worker
    assert isinstance(c2._active_tts_worker, gac.CachedGreetingWorker)


def test_non_greeting_trace_never_touches_cache():
    made = []
    c = _Coordinator(lambda request, parent=None: made.append(request) or _FakeWorker())
    _run(c, trace="turn-1")
    assert made[0].pcm_stream_sink is not None and not isinstance(made[0].pcm_stream_sink, gac.RecordingSink)
    assert not gac.CACHE_DIR.exists()
