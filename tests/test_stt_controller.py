"""SttController 單元測試：以 fake recorder／fake provider 驗證狀態機、
無效音訊過濾、錯誤路徑、重入防護與 shutdown 生命週期。不需 GPU、不需麥克風。

跨 thread 的 pyqtSignal 以 Qt.DirectConnection 連接測試 slot，讓 emit() 在
背景 thread 上同步呼叫 slot，不需要真正跑 Qt event loop 就能觀察狀態序列。
"""

from __future__ import annotations

import pathlib
import threading
import time

import numpy as np
import pytest
from PyQt5.QtCore import Qt

from sensors.base_stt import SttModelLoadError, SttTranscriptionError, TranscriptionResult
from sensors.microphone_recorder import MicrophoneError
from sensors.stt_controller import SttController

_DEFAULT_RESULT = TranscriptionResult(
    text="你好 hello",
    language="zh",
    language_probability=0.9,
    audio_duration_seconds=0.5,
    inference_duration_seconds=0.01,
)


class _FakeRecorder:
    def __init__(self, audio: np.ndarray | None = None) -> None:
        self.device_failed = threading.Event()
        self.max_reached = threading.Event()
        self.start_calls = 0
        self.stop_calls = 0
        self.start_should_raise: Exception | None = None
        self._active = False
        self._audio = audio if audio is not None else np.zeros(8000, dtype=np.float32)  # 500ms @ 16kHz

    def start(self) -> None:
        self.start_calls += 1
        if self.start_should_raise is not None:
            raise self.start_should_raise
        self._active = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._active = False

    def get_audio(self) -> np.ndarray:
        return self._audio

    def shutdown(self) -> None:
        self.stop()

    @property
    def is_active(self) -> bool:
        return self._active


class _FakeProvider:
    def __init__(self) -> None:
        self.setup_calls = 0
        self.shutdown_calls = 0
        self.transcribe_calls = 0
        self.setup_should_raise: Exception | None = None
        self.transcribe_should_raise: Exception | None = None
        self.block_event: threading.Event | None = None
        self.result = _DEFAULT_RESULT
        self._ready = False
        self._last_error = ""

    def setup(self) -> None:
        self.setup_calls += 1
        if self.setup_should_raise is not None:
            self._last_error = str(self.setup_should_raise)
            raise self.setup_should_raise
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscriptionResult:
        self.transcribe_calls += 1
        if self.block_event is not None:
            self.block_event.wait()
        if self.transcribe_should_raise is not None:
            self._last_error = str(self.transcribe_should_raise)
            raise self.transcribe_should_raise
        return self.result

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._ready = False

    @property
    def last_error(self) -> str:
        return self._last_error


def _wait_for(predicate, timeout: float = 1.0, interval: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _make_controller(recorder=None, provider=None, min_recording_ms=100, sample_rate=16000):
    recorder = recorder or _FakeRecorder()
    provider = provider or _FakeProvider()
    controller = SttController(recorder, provider, min_recording_ms=min_recording_ms, sample_rate=sample_rate)
    return controller, recorder, provider


def _join_session(controller: SttController, timeout: float = 1.0) -> None:
    thread = controller._session_thread
    if thread is not None:
        thread.join(timeout=timeout)


# ----------------------------------------------------------------------
# 4.2.1 正常路徑狀態序列
# ----------------------------------------------------------------------


def test_normal_session_sequence_and_single_transcript_submission():
    controller, recorder, provider = _make_controller()
    states: list[str] = []
    transcripts: list[str] = []
    controller.state_changed.connect(states.append, Qt.DirectConnection)
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert states == ["starting", "recording", "stopping", "transcribing", "submitting", "idle"]
    assert transcripts == ["你好 hello"]
    assert recorder.start_calls == 1
    assert recorder.stop_calls == 1


# ----------------------------------------------------------------------
# 4.2.2 無效音訊過濾（三種丟棄原因）
# ----------------------------------------------------------------------


def test_empty_buffer_is_discarded_and_returns_idle():
    recorder = _FakeRecorder(audio=np.zeros(0, dtype=np.float32))
    controller, recorder, provider = _make_controller(recorder=recorder)
    discarded: list[str] = []
    transcripts: list[str] = []
    controller.session_discarded.connect(discarded.append, Qt.DirectConnection)
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert len(discarded) == 1
    assert transcripts == []
    assert provider.transcribe_calls == 0


def test_too_short_recording_is_discarded_and_returns_idle():
    recorder = _FakeRecorder(audio=np.zeros(50, dtype=np.float32))  # 3.125ms @ 16kHz
    controller, recorder, provider = _make_controller(recorder=recorder, min_recording_ms=300)
    discarded: list[str] = []
    transcripts: list[str] = []
    controller.session_discarded.connect(discarded.append, Qt.DirectConnection)
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert len(discarded) == 1
    assert transcripts == []
    assert provider.transcribe_calls == 0


def test_punctuation_only_transcript_is_discarded_and_returns_idle():
    provider = _FakeProvider()
    provider.result = TranscriptionResult(
        text="。！， ",
        language="zh",
        language_probability=0.5,
        audio_duration_seconds=0.5,
        inference_duration_seconds=0.01,
    )
    controller, recorder, provider = _make_controller(provider=provider)
    discarded: list[str] = []
    transcripts: list[str] = []
    controller.session_discarded.connect(discarded.append, Qt.DirectConnection)
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert len(discarded) == 1
    assert transcripts == []


# ----------------------------------------------------------------------
# 4.2.3 錯誤路徑
# ----------------------------------------------------------------------


def test_microphone_open_failure_reports_error_and_recovers():
    recorder = _FakeRecorder()
    recorder.start_should_raise = MicrophoneError("no default input device")
    controller, recorder, provider = _make_controller(recorder=recorder)
    errors: list[str] = []
    controller.error_occurred.connect(errors.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert len(errors) == 1
    assert "麥克風" in errors[0]
    assert "Traceback" not in errors[0]

    # 下一個 session 可正常開始
    recorder.start_should_raise = None
    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)


def test_device_failure_mid_recording_with_enough_audio_still_transcribes():
    controller, recorder, provider = _make_controller(min_recording_ms=100)
    transcripts: list[str] = []
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    recorder.device_failed.set()
    assert _wait_for(lambda: controller.state == "idle", timeout=2.0)
    _join_session(controller)

    assert transcripts == ["你好 hello"]


def test_device_failure_mid_recording_too_short_is_discarded():
    recorder = _FakeRecorder(audio=np.zeros(50, dtype=np.float32))
    controller, recorder, provider = _make_controller(recorder=recorder, min_recording_ms=300)
    discarded: list[str] = []
    transcripts: list[str] = []
    controller.session_discarded.connect(discarded.append, Qt.DirectConnection)
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    recorder.device_failed.set()
    assert _wait_for(lambda: controller.state == "idle", timeout=2.0)
    _join_session(controller)

    assert len(discarded) == 1
    assert transcripts == []


def test_transcription_failure_reports_error_and_recovers():
    provider = _FakeProvider()
    provider.transcribe_should_raise = SttTranscriptionError("cuda out of memory")
    controller, recorder, provider = _make_controller(provider=provider)
    errors: list[str] = []
    transcripts: list[str] = []
    controller.error_occurred.connect(errors.append, Qt.DirectConnection)
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert len(errors) == 1
    assert "辨識失敗" in errors[0]
    assert "Traceback" not in errors[0]
    assert transcripts == []

    # 下一次錄音可正常開始
    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()


def test_model_preload_failure_marks_unavailable():
    provider = _FakeProvider()
    provider.setup_should_raise = SttModelLoadError("libcudnn not found")
    controller, recorder, provider = _make_controller(provider=provider)
    availability: list[bool] = []
    controller.availability_changed.connect(availability.append, Qt.DirectConnection)

    controller.preload_model()
    assert _wait_for(lambda: availability == [False])
    assert "libcudnn" in controller.last_error


def test_model_preload_success_marks_available():
    controller, recorder, provider = _make_controller()
    availability: list[bool] = []
    controller.availability_changed.connect(availability.append, Qt.DirectConnection)

    controller.preload_model()
    assert _wait_for(lambda: availability == [True])


# ----------------------------------------------------------------------
# 4.2.4 重入防護
# ----------------------------------------------------------------------


def test_start_session_is_noop_unless_idle():
    controller, recorder, provider = _make_controller()

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")

    controller.start_session()  # 非 idle，no-op
    controller.start_session()

    assert recorder.start_calls == 1

    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)


def test_start_and_stop_rejected_during_transcribing():
    provider = _FakeProvider()
    provider.block_event = threading.Event()
    controller, recorder, provider = _make_controller(provider=provider)
    transcripts: list[str] = []
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "transcribing")

    controller.start_session()  # 拒絕：非 idle
    controller.stop_session()  # 拒絕：非 recording
    assert controller.state == "transcribing"
    assert recorder.start_calls == 1

    provider.block_event.set()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)
    assert transcripts == ["你好 hello"]


def test_rapid_clicks_produce_at_most_one_submission():
    controller, recorder, provider = _make_controller()
    transcripts: list[str] = []
    controller.transcript_ready.connect(transcripts.append, Qt.DirectConnection)

    for _ in range(5):
        controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    for _ in range(5):
        controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    _join_session(controller)

    assert recorder.start_calls == 1
    assert len(transcripts) <= 1


# ----------------------------------------------------------------------
# 4.2.5 shutdown 生命週期
# ----------------------------------------------------------------------


def test_shutdown_during_recording_stops_stream_and_releases_provider():
    controller, recorder, provider = _make_controller()

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")

    controller.shutdown()

    assert recorder.stop_calls >= 1
    assert provider.shutdown_calls == 1

    # 重複 shutdown 冪等
    controller.shutdown()
    assert provider.shutdown_calls == 1


def test_shutdown_during_transcribing_does_not_block_indefinitely():
    provider = _FakeProvider()
    provider.block_event = threading.Event()
    controller, recorder, provider = _make_controller(provider=provider)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "transcribing")

    started_at = time.monotonic()
    controller.shutdown()
    elapsed = time.monotonic() - started_at

    assert elapsed < 3.0  # join 有 timeout，不會無限阻塞
    assert provider.shutdown_calls == 1

    # 清理仍在背景等待的 thread
    provider.block_event.set()
    _join_session(controller, timeout=1.0)


# ----------------------------------------------------------------------
# 4.2.6 靜態邊界檢查：sensors/ 不依賴 pet_harness.engine
# ----------------------------------------------------------------------


def test_sensors_modules_do_not_depend_on_pet_harness():
    sensors_dir = pathlib.Path(__file__).resolve().parent.parent / "sensors"
    py_files = list(sensors_dir.glob("*.py"))
    assert py_files, "sensors/ 應包含實作檔案"
    for path in py_files:
        content = path.read_text(encoding="utf-8")
        assert "pet_harness" not in content, f"{path} 不得依賴 pet_harness"


# ----------------------------------------------------------------------
# 4.3.5 UI thread 不阻塞、模型只初始化一次
# ----------------------------------------------------------------------


def test_transcribe_runs_off_the_calling_thread_and_does_not_block_it():
    provider = _FakeProvider()
    seen_thread_name = {}
    real_transcribe = provider.transcribe

    def _tracking_transcribe(audio, sample_rate):
        seen_thread_name["name"] = threading.current_thread().name
        time.sleep(0.05)  # 模擬長推論
        return real_transcribe(audio, sample_rate)

    provider.transcribe = _tracking_transcribe
    controller, recorder, provider = _make_controller(provider=provider)
    calling_thread_name = threading.current_thread().name

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")

    started_at = time.monotonic()
    controller.stop_session()  # 呼叫端（模擬 UI thread）立即返回，不等待 transcribe 完成
    elapsed = time.monotonic() - started_at
    assert elapsed < 0.05

    assert _wait_for(lambda: controller.state == "idle", timeout=1.0)
    _join_session(controller)

    assert seen_thread_name["name"] != calling_thread_name


def test_model_never_reinitializes_between_sessions():
    controller, recorder, provider = _make_controller()
    controller.preload_model()
    assert _wait_for(lambda: provider.setup_calls == 1)

    for _ in range(3):
        controller.start_session()
        assert _wait_for(lambda: controller.state == "recording")
        controller.stop_session()
        assert _wait_for(lambda: controller.state == "idle")
        _join_session(controller)

    assert provider.setup_calls == 1
