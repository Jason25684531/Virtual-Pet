"""4.3.4 — STT toggle 整合測試：SttController（真實狀態機 + fake recorder/provider）
接上 TransparentWindow.submit_agentic_text 既有正式入口，驗證：

- 有效 session 的 transcript 恰好呼叫 handle_text_input 一次（test 18/5/6）
- 無效 session（過短）不觸及 handle_text_input（test 8/9，空白／過短已於
  test_stt_controller.py 完整覆蓋三種丟棄原因，此處只需再驗證不觸及既有入口）
- 既有 interaction busy 防護生效時 transcript 被放棄且顯示提示、不重複呼叫（test 4 對應）
- STT 停用／模型載入失敗完全不影響既有打字輸入流程（test 11）

不建構真正的 QApplication/TransparentWindow/QThread：`submit_agentic_text` 直接沿用
TransparentWindow 的正式實作（不重寫防護邏輯），只替換會啟動背景執行緒的
HarnessInteractionWorker 為同步 fake，並以輕量 host 物件提供其協作方法
（沿用 test_developer_input_provider_and_tts.py 的 unbound-method 慣例）。
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from PyQt5.QtCore import Qt, QObject, pyqtSignal

import ui.transparent_window as transparent_window_module
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from sensors.base_stt import SttModelLoadError, TranscriptionResult
from sensors.stt_controller import SttController
from ui.transparent_window import TransparentWindow

from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401 (reused fixture)
from tests.test_stt_controller import _FakeProvider as _FakeSttProvider
from tests.test_stt_controller import _FakeRecorder


class _SyncInteractionWorker(QObject):
    """同步版 HarnessInteractionWorker：不啟動真正的 QThread，避免測試依賴 Qt event loop。"""

    finished_payload = pyqtSignal(dict)
    failed_message = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, adapter, text, parent=None) -> None:
        # ponytail: fake window host 不是真正的 QObject,Qt parent 只接受 QObject/None,
        # 這裡固定不轉發 parent,避免傳入非 QObject 造成 PyQt/SIP 原生崩潰。
        super().__init__(None)
        self._adapter = adapter
        self._text = text

    def start(self) -> None:
        try:
            payload = self._adapter.handle_text_input(self._text)
        except Exception as exc:  # noqa: BLE001
            self.failed_message.emit(str(exc))
        else:
            self.finished_payload.emit(payload)
        self.finished.emit()


class _FakeWindow:
    """只提供 submit_agentic_text 需要的協作方法；submit_agentic_text 本身沿用
    TransparentWindow 的正式實作,不重寫其防護邏輯。"""

    submit_agentic_text = TransparentWindow.submit_agentic_text

    def __init__(self, adapter) -> None:
        self._adapter = adapter
        self._interaction_worker = None
        self.action_statuses: list[tuple[str, str]] = []
        self.agentic_results: list[dict] = []
        self.busy_calls: list[bool] = []

    def set_action_status(self, message, tone="idle", timeout_ms=0):
        self.action_statuses.append((message, tone))

    def _set_agentic_busy(self, busy):
        self.busy_calls.append(busy)

    def _on_agentic_result(self, payload):
        self.agentic_results.append(payload)
        self._set_agentic_busy(False)

    def _on_agentic_error(self, message):
        self.action_statuses.append((message, "error"))
        self._set_agentic_busy(False)

    def _clear_interaction_worker(self):
        self._interaction_worker = None


@pytest.fixture
def fake_window(harness_env, monkeypatch):
    monkeypatch.setattr(transparent_window_module, "HarnessInteractionWorker", _SyncInteractionWorker)
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )
    return _FakeWindow(adapter)


def _make_controller(audio_seconds=0.5, text="你好 hello", min_recording_ms=100):
    recorder = _FakeRecorder(audio=np.zeros(int(16000 * audio_seconds), dtype=np.float32))
    provider = _FakeSttProvider()
    provider.result = TranscriptionResult(
        text=text,
        language="zh",
        language_probability=0.9,
        audio_duration_seconds=audio_seconds,
        inference_duration_seconds=0.01,
    )
    controller = SttController(recorder, provider, min_recording_ms=min_recording_ms, sample_rate=16000)
    return controller, recorder, provider


def _wait_for(predicate, timeout: float = 1.0, interval: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_valid_transcript_reaches_handle_text_input_exactly_once(fake_window):
    controller, _recorder, _provider = _make_controller()
    controller.transcript_ready.connect(fake_window.submit_agentic_text, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    controller._session_thread.join(timeout=1.0)

    assert len(fake_window.agentic_results) == 1
    assert fake_window.agentic_results[0]["user_text"] == "你好 hello"


def test_too_short_session_never_reaches_handle_text_input(fake_window):
    controller, _recorder, _provider = _make_controller(audio_seconds=0.01, min_recording_ms=300)
    controller.transcript_ready.connect(fake_window.submit_agentic_text, Qt.DirectConnection)

    controller.start_session()
    assert _wait_for(lambda: controller.state == "recording")
    controller.stop_session()
    assert _wait_for(lambda: controller.state == "idle")
    controller._session_thread.join(timeout=1.0)

    assert fake_window.agentic_results == []


def test_transcript_during_existing_busy_interaction_is_discarded_with_prompt(fake_window):
    fake_window._interaction_worker = object()  # 模擬既有互動仍在進行中

    fake_window.submit_agentic_text("late transcript")

    assert fake_window.agentic_results == []
    assert fake_window.action_statuses[-1][0] == "Interaction already running."


def test_typing_still_works_when_stt_model_load_failed(fake_window):
    stt_provider = _FakeSttProvider()
    stt_provider.setup_should_raise = SttModelLoadError("libcudnn not found")
    controller = SttController(_FakeRecorder(), stt_provider, min_recording_ms=100, sample_rate=16000)

    controller.preload_model()
    assert _wait_for(lambda: stt_provider.setup_calls == 1)

    fake_window.submit_agentic_text("純打字訊息")

    assert len(fake_window.agentic_results) == 1
    assert fake_window.agentic_results[0]["user_text"] == "純打字訊息"
