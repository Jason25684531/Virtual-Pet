from __future__ import annotations

import hashlib

import numpy as np

from sensors.silero_vad import SileroVad


class _FakeSession:
    def __init__(self, scores: list[float]) -> None:
        self._scores = iter(scores)

    def run(self, _output_names, _inputs):
        score = next(self._scores)
        return [
            np.array([[score]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        ]


class _ExplodingSession:
    def run(self, _output_names, _inputs):
        raise RuntimeError("inference failed")


class _RecordingSession:
    def __init__(self) -> None:
        self.inputs: list[dict] = []

    def run(self, _output_names, inputs):
        self.inputs.append(inputs)
        return [
            np.array([[0.1]], dtype=np.float32),
            np.zeros((2, 1, 128), dtype=np.float32),
        ]


def _ready_vad(tmp_path, scores: list[float], *, silence_ms: int = 800) -> SileroVad:
    model_bytes = b"test-model"
    return SileroVad(
        silence_ms=silence_ms,
        cache_dir=tmp_path,
        downloader=lambda _url, destination: destination.write_bytes(model_bytes),
        session_factory=lambda _model_path: _FakeSession(scores),
        model_sha256=hashlib.sha256(model_bytes).hexdigest(),
    )


def test_silence_never_reports_a_speech_endpoint(tmp_path):
    vad = _ready_vad(tmp_path, [0.1] * 30)

    assert vad.setup() is True
    assert all(not vad.feed_audio(np.zeros(512, dtype=np.float32)) for _ in range(30))


def test_default_silence_requires_500ms_before_an_endpoint(tmp_path):
    model_bytes = b"test-model"
    vad = SileroVad(
        cache_dir=tmp_path,
        downloader=lambda _url, destination: destination.write_bytes(model_bytes),
        session_factory=lambda _model_path: _FakeSession([0.8] * 8 + [0.1] * 25),
        model_sha256=hashlib.sha256(model_bytes).hexdigest(),
    )

    assert vad.setup() is True
    detections = [vad.feed_audio(np.zeros(512, dtype=np.float32)) for _ in range(24)]

    assert detections[-1] is True


def test_frames_include_silero_context_from_the_previous_frame(tmp_path):
    model_bytes = b"test-model"
    session = _RecordingSession()
    vad = SileroVad(
        cache_dir=tmp_path,
        downloader=lambda _url, destination: destination.write_bytes(model_bytes),
        session_factory=lambda _model_path: session,
        model_sha256=hashlib.sha256(model_bytes).hexdigest(),
    )
    first_frame = np.arange(512, dtype=np.float32)
    second_frame = np.arange(512, 1024, dtype=np.float32)

    assert vad.setup() is True
    assert vad.feed_audio(first_frame) is False
    assert vad.feed_audio(second_frame) is False

    assert session.inputs[0]["input"].shape == (1, 576)
    np.testing.assert_array_equal(session.inputs[0]["input"][0, :64], np.zeros(64))
    np.testing.assert_array_equal(session.inputs[1]["input"][0, :64], first_frame[-64:])


def test_speech_followed_by_silence_reports_one_endpoint_and_logs_transitions(tmp_path, capsys):
    vad = _ready_vad(tmp_path, [0.8] * 8 + [0.1] * 25)

    assert vad.setup() is True
    detections = [vad.feed_audio(np.zeros(512, dtype=np.float32)) for _ in range(33)]

    assert detections.count(True) == 1
    assert detections[-1] is True
    logs = capsys.readouterr().out
    assert "[VAD] model ready" in logs
    assert "[VAD] Speech Start detected" in logs
    assert "[VAD] Speech Endpoint detected" in logs


def test_short_burst_does_not_start_speech_or_report_an_endpoint(tmp_path):
    vad = _ready_vad(tmp_path, [0.8] * 7 + [0.1] * 30)

    assert vad.setup() is True
    assert all(not vad.feed_audio(np.zeros(512, dtype=np.float32)) for _ in range(37))


def test_short_pause_during_speech_does_not_report_an_endpoint(tmp_path):
    vad = _ready_vad(tmp_path, [0.8] * 8 + [0.1] * 16 + [0.8] * 8)

    assert vad.setup() is True
    assert all(not vad.feed_audio(np.zeros(512, dtype=np.float32)) for _ in range(32))


def test_long_pause_after_speech_reports_an_endpoint(tmp_path):
    vad = _ready_vad(tmp_path, [0.8] * 8 + [0.1] * 32)

    assert vad.setup() is True
    assert any(vad.feed_audio(np.zeros(512, dtype=np.float32)) for _ in range(40))


def test_missing_model_fails_open(tmp_path):
    vad = SileroVad(
        cache_dir=tmp_path,
        downloader=lambda _url, _destination: (_ for _ in ()).throw(OSError("offline")),
    )

    assert vad.setup() is False
    assert vad.is_ready() is False


def test_inference_failure_disables_vad_without_raising(tmp_path, capsys):
    model_bytes = b"test-model"
    vad = SileroVad(
        cache_dir=tmp_path,
        downloader=lambda _url, destination: destination.write_bytes(model_bytes),
        session_factory=lambda _model_path: _ExplodingSession(),
        model_sha256=hashlib.sha256(model_bytes).hexdigest(),
    )

    assert vad.setup() is True
    assert vad.feed_audio(np.zeros(512, dtype=np.float32)) is False
    assert vad.feed_audio(np.zeros(512, dtype=np.float32)) is False
    assert vad.is_ready() is False
    assert capsys.readouterr().out.count("[VAD] disabled") == 1


def test_shutdown_is_idempotent(tmp_path):
    vad = _ready_vad(tmp_path, [0.1])

    assert vad.setup() is True
    vad.shutdown()
    vad.shutdown()

    assert vad.is_ready() is False
