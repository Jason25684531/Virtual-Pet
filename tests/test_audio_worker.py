"""
AudioStreamWorker 單元測試。

驗證 Thread-Safe Queue 行為：
- 兩段音訊依序播放（FIFO）
- stop() 正常退出，不卡死
- clear_queue() 清除未播項目
- is_busy() 回傳正確狀態
"""

from __future__ import annotations

import io
import threading
import time
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fake audio player：不真正播放，僅記錄呼叫順序和模擬播放耗時
# ---------------------------------------------------------------------------

class FakeAudioPlayer:
    def __init__(self, play_duration: float = 0.05):
        self._duration = play_duration
        self.played: list[str] = []  # 記錄播放順序（以 bytes 識別）
        self._lock = threading.Lock()

    def play(self, audio_buffer: io.BytesIO):
        audio_buffer.seek(0)
        tag = audio_buffer.read().decode("utf-8", errors="replace")
        with self._lock:
            self.played.append(tag)
        time.sleep(self._duration)


class FakePcmPlayer:
    def __init__(self):
        self.chunks: list[bytes] = []

    def play_chunks(self, chunks, before_start=None):
        started = False
        total = 0
        for chunk in chunks:
            if not chunk:
                continue
            if not started:
                started = True
                if callable(before_start):
                    before_start()
            self.chunks.append(bytes(chunk))
            total += len(chunk)
        return total


class InterruptiblePcmPlayer:
    def __init__(self):
        self.chunks: list[bytes] = []
        self.second_chunk_started = threading.Event()
        self.release_second_chunk = threading.Event()

    def play_chunks(self, chunks, before_start=None):
        started = False
        total = 0
        for index, chunk in enumerate(chunks):
            if not chunk:
                continue
            if not started:
                started = True
                if callable(before_start):
                    before_start()
            if index == 1:
                self.second_chunk_started.set()
                self.release_second_chunk.wait(1.0)
            payload = bytes(chunk)
            self.chunks.append(payload)
            total += len(payload)
        return total


class SignalCollector:
    def __init__(self):
        self.events = []

    def __call__(self, *args):
        self.events.append(args)


# ---------------------------------------------------------------------------
# 延遲匯入 AudioStreamWorker（避免 PyQt5 初始化問題）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def fake_player():
    return FakeAudioPlayer(play_duration=0.05)


@pytest.fixture
def worker(qapp, fake_player):
    from audio_worker import AudioStreamWorker
    w = AudioStreamWorker(audio_player=fake_player)
    w.start()
    yield w, fake_player
    w.stop()
    w.wait(2000)  # 等待 daemon thread 退出（最多 2 秒）


# ---------------------------------------------------------------------------
# 測試案例
# ---------------------------------------------------------------------------

def test_fifo_order(worker):
    """兩段音訊應依 enqueue 順序播放。"""
    w, player = worker

    buf1 = io.BytesIO(b"first")
    buf2 = io.BytesIO(b"second")
    w.enqueue(buf1, reply_id="r1")
    w.enqueue(buf2, reply_id="r2")

    # 等待兩段都播完（2 * 0.05s + buffer）
    timeout = time.time() + 2.0
    while len(player.played) < 2 and time.time() < timeout:
        time.sleep(0.02)

    assert player.played == ["first", "second"], f"播放順序錯誤: {player.played}"


def test_stop_exits_cleanly(qapp, fake_player):
    """stop() 後 wait() 應在合理時間內返回，不卡死。"""
    from audio_worker import AudioStreamWorker
    w = AudioStreamWorker(audio_player=fake_player)
    w.start()
    assert w.isRunning()
    w.stop()
    exited = w.wait(1000)  # 1 秒內應正常退出
    assert exited, "AudioStreamWorker.stop() 後未在 1 秒內退出"
    assert not w.isRunning()


def test_clear_queue(qapp):
    """clear_queue() 應清除佇列中尚未播放的項目。"""
    slow_player = FakeAudioPlayer(play_duration=0.3)

    from audio_worker import AudioStreamWorker
    w = AudioStreamWorker(audio_player=slow_player)
    w.start()

    for i in range(3):
        w.enqueue(io.BytesIO(f"item{i}".encode()), reply_id=f"r{i}")

    time.sleep(0.05)  # 等 item0 開始播放
    w.clear_queue()   # 清除 item1 / item2

    w.stop()
    w.wait(2000)

    assert len(slow_player.played) <= 1, f"clear_queue 後不應有多餘播放: {slow_player.played}"


def test_is_busy(qapp, fake_player):
    """is_busy() 應在有項目時回傳 True，清空後回傳 False。"""
    from audio_worker import AudioStreamWorker
    w = AudioStreamWorker(audio_player=fake_player)
    w.start()

    assert not w.is_busy()

    w.enqueue(io.BytesIO(b"test"), reply_id="r1")
    time.sleep(0.01)
    assert w.is_busy()

    # 等播完
    timeout = time.time() + 2.0
    while w.is_busy() and time.time() < timeout:
        time.sleep(0.02)
    assert not w.is_busy()

    w.stop()
    w.wait(1000)


def test_concurrent_enqueue_is_safe(worker):
    """多個 Thread 同時 enqueue 不應造成卡死或資料遺失。"""
    w, player = worker

    results = []
    errors = []

    def enqueue_batch(start: int):
        try:
            for i in range(5):
                buf = io.BytesIO(f"t{start + i}".encode())
                w.enqueue(buf, reply_id=f"r{start + i}")
            results.append(True)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=enqueue_batch, args=(i * 5,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"並發 enqueue 出現例外: {errors}"

    # 等待播完所有 20 個項目
    timeout = time.time() + 5.0
    while len(player.played) < 20 and time.time() < timeout:
        time.sleep(0.05)

    assert len(player.played) == 20, f"並發 enqueue 後播放數量錯誤: {len(player.played)}"


def test_pcm_trace_session_emits_single_driver_started_and_queue_drained(qapp, fake_player):
    from audio_worker import AudioStreamWorker

    pcm_player = FakePcmPlayer()
    driver_started = SignalCollector()
    playback_finished = SignalCollector()
    queue_drained = SignalCollector()

    worker = AudioStreamWorker(
        audio_player=fake_player,
        pcm_player_factory=lambda sample_rate, channels: pcm_player,
        pcm_session_idle_ms=0,
    )
    worker.driver_started.connect(driver_started)
    worker.playback_finished.connect(playback_finished)
    worker.queue_drained.connect(queue_drained)
    worker.start()

    worker.enqueue_pcm_chunk(b"pcm-a", reply_id="r1", trace_id="trace-1")
    worker.finish_pcm_segment("r1", trace_id="trace-1")
    worker.enqueue_pcm_chunk(b"pcm-b", reply_id="r2", trace_id="trace-1")
    worker.finish_pcm_segment("r2", trace_id="trace-1")
    worker.close_trace_session("trace-1")

    timeout = time.time() + 2.0
    while len(playback_finished.events) < 2 and time.time() < timeout:
        qapp.processEvents()
        time.sleep(0.02)

    worker.stop()
    worker.wait(1000)
    qapp.processEvents()

    assert pcm_player.chunks == [b"pcm-a", b"pcm-b"]
    assert driver_started.events == [("r1", "trace-1")]
    assert playback_finished.events == [("r1", "trace-1"), ("r2", "trace-1")]
    assert len(queue_drained.events) == 1


def test_interrupt_trace_preserves_finished_pcm_segment_and_drops_later_one(qapp, fake_player):
    from audio_worker import AudioStreamWorker

    pcm_player = InterruptiblePcmPlayer()
    playback_finished = SignalCollector()

    worker = AudioStreamWorker(
        audio_player=fake_player,
        pcm_player_factory=lambda sample_rate, channels: pcm_player,
        pcm_session_idle_ms=0,
    )
    worker.playback_finished.connect(playback_finished)
    worker.start()

    worker.enqueue_pcm_chunk(b"first-segment", reply_id="r1", trace_id="trace-int")
    worker.finish_pcm_segment("r1", trace_id="trace-int")

    timeout = time.time() + 2.0
    while len(playback_finished.events) < 1 and time.time() < timeout:
        qapp.processEvents()
        time.sleep(0.02)

    worker.enqueue_pcm_chunk(b"second-segment", reply_id="r2", trace_id="trace-int")
    worker.finish_pcm_segment("r2", trace_id="trace-int")
    assert pcm_player.second_chunk_started.wait(1.0), "第二段 PCM 沒有進入 active session"

    worker.interrupt_trace("trace-int")
    pcm_player.release_second_chunk.set()

    timeout = time.time() + 2.0
    while worker.is_busy() and time.time() < timeout:
        qapp.processEvents()
        time.sleep(0.02)

    worker.stop()
    worker.wait(1000)
    qapp.processEvents()

    assert playback_finished.events == [("r1", "trace-int")]
