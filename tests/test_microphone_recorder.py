"""MicrophoneRecorder 單元測試：monkeypatch sounddevice.InputStream,
不需要真實麥克風硬體。callback 直接手動呼叫模擬音訊到達。"""

from __future__ import annotations

import numpy as np
import pytest
import sounddevice as sd

from sensors.microphone_recorder import MicrophoneError, MicrophoneRecorder


class _FakeInputStream:
    def __init__(self, samplerate, channels, dtype, callback):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


def _patch_stream(monkeypatch, stream_holder: list):
    def _factory(samplerate, channels, dtype, callback):
        stream = _FakeInputStream(samplerate, channels, dtype, callback)
        stream_holder.append(stream)
        return stream

    monkeypatch.setattr("sensors.microphone_recorder.sd.InputStream", _factory)


def test_start_stop_produces_single_concatenated_buffer(monkeypatch):
    streams: list[_FakeInputStream] = []
    _patch_stream(monkeypatch, streams)

    recorder = MicrophoneRecorder(sample_rate=16000, max_recording_seconds=30)
    recorder.start()
    assert recorder.is_active is True
    assert streams[0].started is True

    chunk1 = np.ones((160, 1), dtype=np.float32)
    chunk2 = np.ones((160, 1), dtype=np.float32) * 2
    recorder._on_audio(chunk1, 160, None, None)
    recorder._on_audio(chunk2, 160, None, None)

    recorder.stop()
    assert recorder.is_active is False
    assert streams[0].closed is True

    audio = recorder.get_audio()
    assert audio.shape == (320,)
    assert audio.dtype == np.float32


def test_device_open_failure_raises_microphone_error(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("no default input device")

    monkeypatch.setattr("sensors.microphone_recorder.sd.InputStream", lambda **kw: _boom())

    recorder = MicrophoneRecorder(sample_rate=16000, max_recording_seconds=30)
    with pytest.raises(MicrophoneError):
        recorder.start()
    assert recorder.is_active is False


def test_max_recording_seconds_auto_stops(monkeypatch):
    streams: list[_FakeInputStream] = []
    _patch_stream(monkeypatch, streams)

    # sample_rate=100, max_recording_seconds=1 -> max_samples=100
    recorder = MicrophoneRecorder(sample_rate=100, max_recording_seconds=1)
    recorder.start()

    chunk = np.ones((60, 1), dtype=np.float32)
    recorder._on_audio(chunk, 60, None, None)
    assert recorder.max_reached.is_set() is False

    with pytest.raises(sd.CallbackStop):
        recorder._on_audio(chunk, 60, None, None)
    assert recorder.max_reached.is_set() is True

    # 達上限後即便再送 chunk 也不再增長
    audio = recorder.get_audio()
    assert audio.shape[0] == 100


def test_device_failure_mid_recording_sets_flag_and_stops_callback(monkeypatch):
    streams: list[_FakeInputStream] = []
    _patch_stream(monkeypatch, streams)

    recorder = MicrophoneRecorder(sample_rate=16000, max_recording_seconds=30)
    recorder.start()

    chunk = np.ones((160, 1), dtype=np.float32)
    recorder._on_audio(chunk, 160, None, None)

    with pytest.raises(sd.CallbackStop):
        recorder._on_audio(chunk, 160, None, status="input overflow")

    assert recorder.device_failed.is_set() is True
    assert recorder.is_active is False
    # 裝置失效前已收的 buffer 仍可取得,由 controller 判斷是否達最短長度可用
    audio = recorder.get_audio()
    assert audio.shape[0] == 160


def test_repeated_start_is_noop_while_active(monkeypatch):
    streams: list[_FakeInputStream] = []
    _patch_stream(monkeypatch, streams)

    recorder = MicrophoneRecorder(sample_rate=16000, max_recording_seconds=30)
    recorder.start()
    recorder.start()

    assert len(streams) == 1


def test_shutdown_closes_stream(monkeypatch):
    streams: list[_FakeInputStream] = []
    _patch_stream(monkeypatch, streams)

    recorder = MicrophoneRecorder(sample_rate=16000, max_recording_seconds=30)
    recorder.start()
    recorder.shutdown()

    assert recorder.is_active is False
    assert streams[0].closed is True
