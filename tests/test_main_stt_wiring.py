"""4.3.2 — main.py::_build_stt_controller 組裝驗證：STT_ENABLED=false 時零 STT 物件
建立；STT_ENABLED=true 時 FasterWhisperSTT/MicrophoneRecorder/SttController 正確
依賴注入，controller signals 正確接線到既有 UI 入口。

以 monkeypatch 替換 sensors 的 concrete class 與 fake window（MagicMock），
不建構真正的 QApplication/TransparentWindow。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import config
import main


def _fake_window():
    window = MagicMock()
    return window


def test_stt_disabled_creates_zero_stt_objects(monkeypatch):
    monkeypatch.setattr(config, "STT_ENABLED", False)
    monkeypatch.setattr(config, "STT_VAD_ENABLED", False)
    fw_ctor = MagicMock()
    mic_ctor = MagicMock()
    controller_ctor = MagicMock()
    vad_ctor = MagicMock()
    monkeypatch.setattr("sensors.faster_whisper_stt.FasterWhisperSTT", fw_ctor)
    monkeypatch.setattr("sensors.microphone_recorder.MicrophoneRecorder", mic_ctor)
    monkeypatch.setattr("sensors.stt_controller.SttController", controller_ctor)
    monkeypatch.setattr("sensors.silero_vad.SileroVad", vad_ctor)
    window = _fake_window()

    result = main._build_stt_controller(window)

    assert result is None
    fw_ctor.assert_not_called()
    mic_ctor.assert_not_called()
    controller_ctor.assert_not_called()
    vad_ctor.assert_not_called()
    window.set_stt_available.assert_called_once_with(False)
    window.set_stt_controller.assert_not_called()


def test_stt_enabled_wires_controller_signals_to_existing_ui_entries(monkeypatch):
    monkeypatch.setattr(config, "STT_ENABLED", True)
    monkeypatch.setattr(config, "STT_VAD_ENABLED", False)
    fake_provider = MagicMock(name="provider")
    fake_recorder = MagicMock(name="recorder")
    fake_controller = MagicMock(name="controller")
    monkeypatch.setattr(
        "sensors.faster_whisper_stt.FasterWhisperSTT", MagicMock(return_value=fake_provider)
    )
    monkeypatch.setattr(
        "sensors.microphone_recorder.MicrophoneRecorder", MagicMock(return_value=fake_recorder)
    )
    monkeypatch.setattr(
        "sensors.stt_controller.SttController", MagicMock(return_value=fake_controller)
    )
    window = _fake_window()

    result = main._build_stt_controller(window)

    assert result is fake_controller
    window.stt_start_requested.connect.assert_called_once_with(fake_controller.start_session)
    window.stt_stop_requested.connect.assert_called_once_with(fake_controller.stop_session)
    fake_controller.availability_changed.connect.assert_called_once_with(window.set_stt_available)
    fake_controller.transcript_ready.connect.assert_called_once_with(window.submit_agentic_text)
    fake_controller.state_changed.connect.assert_called_once()
    fake_controller.session_discarded.connect.assert_called_once()
    fake_controller.error_occurred.connect.assert_called_once()
    window.set_stt_controller.assert_called_once_with(fake_controller)
    fake_controller.preload_model.assert_called_once()
    # 模型 preload 完成前顯示「載入中」而非「不可用」——後者讀起來像永久壞掉。
    # 等 availability_changed 訊號才轉為可用；只有載入失敗才會變成 unavailable。
    window.set_stt_state.assert_called_once_with("loading")
    window.set_stt_available.assert_not_called()


def test_vad_enabled_creates_and_injects_vad(monkeypatch):
    monkeypatch.setattr(config, "STT_ENABLED", True)
    monkeypatch.setattr(config, "STT_VAD_ENABLED", True)
    monkeypatch.setattr(config, "STT_VAD_SILENCE_MS", 800)
    monkeypatch.setattr(config, "STT_VAD_THRESHOLD", 0.5)
    fake_vad = MagicMock(name="vad")
    vad_ctor = MagicMock(return_value=fake_vad)
    controller_ctor = MagicMock(return_value=MagicMock(name="controller"))
    monkeypatch.setattr("sensors.silero_vad.SileroVad", vad_ctor)
    monkeypatch.setattr(
        "sensors.faster_whisper_stt.FasterWhisperSTT", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        "sensors.microphone_recorder.MicrophoneRecorder", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr("sensors.stt_controller.SttController", controller_ctor)

    main._build_stt_controller(_fake_window())

    vad_ctor.assert_called_once_with(silence_ms=800, threshold=0.5)
    assert controller_ctor.call_args.kwargs["vad"] is fake_vad


def test_vad_preload_runs_in_a_separate_background_thread(monkeypatch):
    monkeypatch.setattr(config, "STT_ENABLED", True)
    monkeypatch.setattr(config, "STT_VAD_ENABLED", True)
    fake_vad = MagicMock(name="vad")
    monkeypatch.setattr("sensors.silero_vad.SileroVad", MagicMock(return_value=fake_vad))
    monkeypatch.setattr(
        "sensors.faster_whisper_stt.FasterWhisperSTT", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        "sensors.microphone_recorder.MicrophoneRecorder", MagicMock(return_value=MagicMock())
    )
    fake_controller = MagicMock(name="controller")
    monkeypatch.setattr("sensors.stt_controller.SttController", MagicMock(return_value=fake_controller))
    threads: list[MagicMock] = []

    def _thread(*, target, daemon, name):
        thread = MagicMock()
        thread.target = target
        thread.daemon = daemon
        thread.name = name
        threads.append(thread)
        return thread

    monkeypatch.setattr(main.threading, "Thread", _thread)

    main._build_stt_controller(_fake_window())

    assert len(threads) == 1
    assert threads[0].target == fake_vad.setup
    assert threads[0].daemon is True
    assert threads[0].name == "VadPreload"
    threads[0].start.assert_called_once()
    fake_controller.preload_model.assert_called_once()


def test_preload_stt_provider_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(config, "STT_ENABLED", False)

    assert main._preload_stt_provider() is None


def test_preload_stt_provider_calls_setup_before_qapplication_exists(monkeypatch):
    """CUDA 原生初始化必須發生在 QApplication 建構前(見 main.py 內的說明),
    這裡驗證 _preload_stt_provider 會同步呼叫 provider.setup(),
    且回傳同一個實例供 _build_stt_controller 重用、不重新初始化模型。"""
    monkeypatch.setattr(config, "STT_ENABLED", True)
    fake_provider = MagicMock(name="provider")
    monkeypatch.setattr(
        "sensors.faster_whisper_stt.FasterWhisperSTT", MagicMock(return_value=fake_provider)
    )

    result = main._preload_stt_provider()

    assert result is fake_provider
    fake_provider.setup.assert_called_once()


def test_build_stt_controller_reuses_preloaded_provider(monkeypatch):
    monkeypatch.setattr(config, "STT_ENABLED", True)
    monkeypatch.setattr(config, "STT_VAD_ENABLED", False)
    fw_ctor = MagicMock()
    monkeypatch.setattr("sensors.faster_whisper_stt.FasterWhisperSTT", fw_ctor)
    monkeypatch.setattr(
        "sensors.microphone_recorder.MicrophoneRecorder", MagicMock(return_value=MagicMock())
    )
    fake_provider = MagicMock(name="preloaded_provider")
    controller_ctor = MagicMock(return_value=MagicMock(name="controller"))
    monkeypatch.setattr("sensors.stt_controller.SttController", controller_ctor)

    main._build_stt_controller(_fake_window(), provider=fake_provider)

    fw_ctor.assert_not_called()
    assert controller_ctor.call_args.args[1] is fake_provider


def test_state_changed_mapping_translates_recording_to_listening(monkeypatch):
    """controller 的 RecordingState 用字（recording）與既有 UI 白名單（listening）不同，
    submitting/error 不在白名單內、set_stt_state 既有 fallback 會自動視為 idle。"""
    monkeypatch.setattr(config, "STT_ENABLED", True)
    monkeypatch.setattr(config, "STT_VAD_ENABLED", False)
    fake_controller = MagicMock(name="controller")
    monkeypatch.setattr(
        "sensors.faster_whisper_stt.FasterWhisperSTT", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        "sensors.microphone_recorder.MicrophoneRecorder", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        "sensors.stt_controller.SttController", MagicMock(return_value=fake_controller)
    )
    window = _fake_window()

    main._build_stt_controller(window)

    state_changed_callback = fake_controller.state_changed.connect.call_args[0][0]

    state_changed_callback("recording")
    window.set_stt_state.assert_called_with("listening")

    state_changed_callback("transcribing")
    window.set_stt_state.assert_called_with("transcribing")

    state_changed_callback("idle")
    window.set_stt_state.assert_called_with("idle")
