"""
Application entrypoint for the ECHOES desktop host runtime.
"""

from __future__ import annotations

import signal
import sys
import threading


def _configure_sigint_timer(app):
    from PyQt5.QtCore import QTimer

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.setQuitOnLastWindowClosed(False)
    app._sigint_timer = QTimer(parent=app)
    app._sigint_timer.start(200)
    app._sigint_timer.timeout.connect(lambda: None)


def _create_application(argv):
    import os
    from PyQt5.QtCore import QCoreApplication, Qt
    from PyQt5.QtWidgets import QApplication

    # 禁用 Qt 自動 DPI 縮放，讓視窗以物理像素為準
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QCoreApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
    return QApplication(argv)


def _build_stt_controller(window):
    """STT_ENABLED=false 時完全不建立任何 STT 物件（按鈕維持 unavailable）。"""
    import config

    if not config.STT_ENABLED:
        print("[STT] STT_ENABLED=false，不建立任何 STT 物件。")
        window.set_stt_available(False)
        return None

    print(
        f"[STT] 組裝 controller：model={config.STT_MODEL} device={config.STT_DEVICE} "
        f"compute_type={config.STT_COMPUTE_TYPE} sample_rate={config.STT_SAMPLE_RATE}"
    )

    from sensors.faster_whisper_stt import FasterWhisperSTT
    from sensors.microphone_recorder import MicrophoneRecorder
    from sensors.stt_controller import SttController

    vad = None
    if config.STT_VAD_ENABLED:
        from sensors.silero_vad import SileroVad

        vad = SileroVad(
            silence_ms=config.STT_VAD_SILENCE_MS,
            threshold=config.STT_VAD_THRESHOLD,
        )
        print(
            f"[STT] VAD enabled: silence_ms={config.STT_VAD_SILENCE_MS} "
            f"threshold={config.STT_VAD_THRESHOLD}"
        )

    provider = FasterWhisperSTT(
        config.STT_MODEL,
        config.STT_DEVICE,
        config.STT_COMPUTE_TYPE,
        config.STT_MODEL_PATH,
        language=config.STT_LANGUAGE or None,
        beam_size=config.STT_BEAM_SIZE,
    )
    recorder = MicrophoneRecorder(
        sample_rate=config.STT_SAMPLE_RATE,
        max_recording_seconds=config.STT_MAX_RECORDING_SECONDS,
    )
    controller = SttController(
        recorder,
        provider,
        min_recording_ms=config.STT_MIN_RECORDING_MS,
        sample_rate=config.STT_SAMPLE_RATE,
        vad=vad,
    )

    window.set_stt_available(False)  # 模型 preload 完成前維持 unavailable
    window.stt_start_requested.connect(controller.start_session)
    window.stt_stop_requested.connect(controller.stop_session)
    # controller 的 RecordingState 用字與既有 UI 白名單不完全相同（recording -> listening），
    # submitting/error 不在 UI 白名單內、set_stt_state 既有 fallback 會自動視為 idle。
    controller.state_changed.connect(
        lambda state: window.set_stt_state("listening" if state == "recording" else state)
    )
    controller.availability_changed.connect(window.set_stt_available)
    controller.transcript_ready.connect(window.submit_agentic_text)
    controller.session_discarded.connect(
        lambda reason: window.set_action_status(reason, tone="warn", timeout_ms=3200)
    )
    controller.error_occurred.connect(
        lambda message: window.set_action_status(message, tone="warn", timeout_ms=3200)
    )
    window.set_stt_controller(controller)
    if vad is not None:
        # VAD 為選配的 fail-open 元件，setup 不可延遲 UI 或 STT preload。
        threading.Thread(target=vad.setup, daemon=True, name="VadPreload").start()
    controller.preload_model()
    return controller


def _run_harness_mode(app):
    from character_library import CharacterLibrary
    from action_dispatcher import MotionCoordinator
    from interaction_trace import InteractionLatencyTracker
    from pet_harness.app.application_coordinator import ApplicationCoordinator
    from pet_harness.app.runtime_lifecycle import CallbackRuntime
    from pet_harness.runtime.qt_background_executor import QtBackgroundExecutor
    from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter, _qdrant_memory_store_factory
    from ui.presentation_event_binder import PresentationEventBinder
    from ui.motion_port_adapter import MotionPortAdapter
    from pet_harness.voice_runtime_status_adapter import VoiceRuntimeStatusAdapter
    from ui.transparent_window import TransparentWindow

    latency_tracker = InteractionLatencyTracker()
    coordinator = ApplicationCoordinator(
        default_character_id="Choppr",
        memory_store_factory=_qdrant_memory_store_factory,
        semantic_index_enabled=True,
    )
    adapter = PyQtHarnessAdapter(
        provider_runtime=coordinator.provider_runtime,
        character_router=coordinator.character_router,
        character_registry=coordinator.character_registry,
    )
    library = CharacterLibrary()
    window = TransparentWindow(
        brain_mode="harness",
        latency_tracker=latency_tracker,
        library=library,
        adapter=adapter,
        lifecycle_shutdown=coordinator.shutdown,
        action_bus=coordinator.action_bus,
    )
    motion = MotionCoordinator(window, library, latency_tracker=latency_tracker, parent=window)
    window.configure_motion(motion)
    coordinator.configure_motion(MotionPortAdapter(motion, window))
    PresentationEventBinder(window, coordinator.event_bus)
    executor = QtBackgroundExecutor(window)
    coordinator.configure_conversation(adapter, executor)

    stt_controller = _build_stt_controller(window)
    coordinator.lifecycle.register(CallbackRuntime("adapter", lambda _wait_ms: adapter.shutdown()))
    coordinator.lifecycle.register(CallbackRuntime("motion", motion.shutdown))
    if stt_controller is not None:
        coordinator.lifecycle.register(CallbackRuntime("stt", lambda _wait_ms: stt_controller.shutdown()))
    coordinator.lifecycle.register(CallbackRuntime("router", lambda _wait_ms: coordinator.character_router.shutdown()))
    coordinator.lifecycle.register(executor)

    window.configure_runtime_context(
        runtime_contract=None,
        live_runtime_available=False,
        voice_status_adapter=VoiceRuntimeStatusAdapter(stt_controller=stt_controller),
    )
    window.show()
    window.set_action_status("Harness mode ready.", tone="idle", timeout_ms=2400)
    app.aboutToQuit.connect(coordinator.shutdown)
    return window


def _preload_onnx_runtime():
    """onnxruntime 的原生 DLL 必須在 QApplication 建構前完成載入,否則在 Windows 上
    會與 Qt 的原生依賴衝突,導致 DLL 初始化失敗(順序測試已於
    fix-core-interaction-experience 驗證重現)。語意 skill 路由與對話記憶都依賴
    onnxruntime,兩者本身已各自 fail-open 退化,這裡預先載入只是確保它們有機會
    真正就緒,而不是每次都因載入順序而永遠停用;找不到套件時安靜跳過。"""
    try:
        import onnxruntime  # noqa: F401
        import qdrant_client  # noqa: F401
    except ImportError:
        pass


def main():
    print("[ECHOES] brain mode: harness")
    _preload_onnx_runtime()

    app = _create_application(sys.argv)
    _configure_sigint_timer(app)
    _run_harness_mode(app)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
