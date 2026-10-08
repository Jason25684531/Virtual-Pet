"""
Application entrypoint for the ECHOES desktop host runtime.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import threading
from pathlib import Path


def _configure_sigint_timer(app):
    from PyQt5.QtCore import QTimer

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.setQuitOnLastWindowClosed(False)
    app._sigint_timer = QTimer(parent=app)
    app._sigint_timer.start(200)
    app._sigint_timer.timeout.connect(lambda: None)


def _schedule_youtube_warmup(executor) -> None:
    """Best-effort startup check; it does not promise ad-free playback."""
    from PyQt5.QtCore import QTimer
    from pet_harness.tools.youtube_music_tool import warmup_once

    def done(ok, result) -> None:
        if not ok:
            print(f"[YOUTUBE WARMUP] failed: {result}")
        elif result is None:
            print("[YOUTUBE WARMUP] failed: no result")
        elif result.status in {"success", "partial"}:
            print(f"[YOUTUBE WARMUP] {result.status}: browser closed")
        else:
            print(f"[YOUTUBE WARMUP] {result.status}: {result.error}")

    def run() -> None:
        try:
            executor.submit(warmup_once, done)
        except RuntimeError:
            # The application may have started shutting down before the timer fired.
            return

    QTimer.singleShot(1000, run)


def _create_application(argv):
    from PyQt5.QtCore import QCoreApplication, Qt
    from PyQt5.QtWidgets import QApplication

    # 禁用 Qt 自動 DPI 縮放，讓視窗以物理像素為準
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QCoreApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
    return QApplication(argv)


def _preload_stt_provider():
    """faster-whisper（ctranslate2）的 CUDA 原生初始化如果發生在 QApplication 建構之後，
    會在這台機器上直接讓整個行程 segfault（實測與 _preload_onnx_runtime 的 DLL 衝突同一類
    問題，但 ctranslate2 目前沒有等效的 preload）。故在 QApplication 建構前，於這裡先同步
    載入模型；_build_stt_controller 之後重用同一個 provider 實例，不再重新初始化。"""
    import config

    if not config.STT_ENABLED:
        return None

    from sensors.faster_whisper_stt import FasterWhisperSTT

    provider = FasterWhisperSTT(
        config.STT_MODEL,
        config.STT_DEVICE,
        config.STT_COMPUTE_TYPE,
        config.STT_MODEL_PATH,
        language=config.STT_LANGUAGE or None,
        beam_size=config.STT_BEAM_SIZE,
        warmup=True,
    )
    print(f"[STT] 於 QApplication 建構前預先載入模型：model={config.STT_MODEL} device={config.STT_DEVICE}")
    try:
        provider.setup()
    except Exception as exc:  # noqa: BLE001
        print(f"[STT] 模型預先載入失敗，STT 將標記為不可用: {exc}")
    return provider


def _build_stt_controller(window, provider=None):
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

    from sensors.microphone_recorder import MicrophoneRecorder
    from sensors.stt_controller import SttController

    vad = None
    if config.STT_VAD_ENABLED:
        from sensors.silero_vad import SileroVad

        vad = SileroVad(
            silence_ms=config.STT_VAD_SILENCE_MS,
            threshold=config.STT_VAD_THRESHOLD,
            cache_dir=Path(config.STT_VAD_MODEL_DIR),
        )
        silence_source = "env" if os.getenv("STT_VAD_SILENCE_MS") is not None else "default"
        print(
            f"[STT] VAD enabled: silence_ms={config.STT_VAD_SILENCE_MS} "
            f"threshold={config.STT_VAD_THRESHOLD}"
        )
        print(f"[VAD CONFIG] silence_ms={config.STT_VAD_SILENCE_MS} source={silence_source}")

    if provider is None:
        from sensors.faster_whisper_stt import FasterWhisperSTT

        provider = FasterWhisperSTT(
            config.STT_MODEL,
            config.STT_DEVICE,
            config.STT_COMPUTE_TYPE,
            config.STT_MODEL_PATH,
            language=config.STT_LANGUAGE or None,
            beam_size=config.STT_BEAM_SIZE,
            warmup=True,
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

    window.set_stt_state("loading")  # 模型 preload 完成前顯示「載入中」，失敗才轉為不可用
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


def _run_harness_mode(app, stt_provider=None):
    from character_library import CharacterLibrary
    from action_dispatcher import MotionCoordinator
    from interaction_trace import InteractionLatencyTracker
    from pet_harness.app.application_coordinator import ApplicationCoordinator
    from pet_harness.app.runtime_lifecycle import CallbackRuntime
    from pet_harness.runtime.qt_background_executor import QtBackgroundExecutor
    from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter, _qdrant_memory_store_factory
    from ui.presentation_wiring import MotionPortAdapter, PresentationEventBinder
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
        latency_tracker=latency_tracker,
        library=library,
        adapter=adapter,
        lifecycle_shutdown=coordinator.shutdown,
        action_bus=coordinator.action_bus,
    )
    motion = MotionCoordinator(
        window,
        library,
        latency_tracker=latency_tracker,
        provider_runtime=coordinator.provider_runtime,
        parent=window,
    )
    window.configure_motion(motion)
    adapter.configure_streaming(motion.enqueue_stream_chunk, motion.enqueue_stream_action)
    coordinator.configure_motion(MotionPortAdapter(motion, window))
    PresentationEventBinder(window, coordinator.event_bus)
    executor = QtBackgroundExecutor(window)
    coordinator.configure_conversation(adapter, executor)
    adapter.configure_background_executor(executor)

    stt_controller = _build_stt_controller(window, provider=stt_provider)
    if stt_controller is not None:
        stt_controller.voice_turn_timing.connect(adapter.register_voice_turn_timing)
    coordinator.lifecycle.register(CallbackRuntime("adapter", lambda _wait_ms: adapter.shutdown()))
    coordinator.lifecycle.register(CallbackRuntime("motion", motion.shutdown))
    if stt_controller is not None:
        coordinator.lifecycle.register(CallbackRuntime("stt", lambda _wait_ms: stt_controller.shutdown()))
    coordinator.lifecycle.register(CallbackRuntime("router", lambda _wait_ms: coordinator.character_router.shutdown()))
    coordinator.lifecycle.register(executor)

    window.configure_runtime_context(
        voice_status_adapter=VoiceRuntimeStatusAdapter(stt_controller=stt_controller),
    )
    window.show()
    _schedule_youtube_warmup(executor)
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
    # 必須在 _preload_onnx_runtime 匯入 qdrant_client→huggingface_hub 之前：config 會設定
    # HF_HUB_OFFLINE / FASTEMBED_CACHE_PATH，而 huggingface_hub 只在 import 當下讀一次。
    import config  # noqa: F401

    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                log_dir / "echoes.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            ),
        ],
    )
    print("[ECHOES] brain mode: harness")
    _preload_onnx_runtime()
    stt_provider = _preload_stt_provider()

    app = _create_application(sys.argv)
    _configure_sigint_timer(app)
    _run_harness_mode(app, stt_provider=stt_provider)
    sys.exit(app.exec_())


if __name__ == "__main__":
    # Release 的 stdout/stderr 被導向 logs/*.log：預設用系統 ANSI 編碼（如 cp1252）會在印中文時崩潰；
    # 預設是區塊緩衝，PyQt 遇到未處理例外會 abort，緩衝中的 traceback 會跟著消失，所以逐行寫入。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    # .agentic、data/runtime 等是相對 CWD 的路徑；從捷徑或其他目錄啟動 Release EXE 時也要落在 App 根目錄。
    _app_root = Path(__file__).resolve().parent
    os.chdir(_app_root)
    # Release 內附 ffmpeg/ffplay.exe 與 CUDA DLL（nvidia/*/bin）時優先使用，新機不需另外安裝。
    # nvidia.* 是沒有程式碼的 namespace package，Nuitka 不會帶，faster_whisper_stt 找不到時改由這裡補 PATH。
    _bundled_bins = [_app_root / "ffmpeg", *sorted((_app_root / "nvidia").glob("*/bin"))]
    os.environ["PATH"] = os.pathsep.join([*(str(p) for p in _bundled_bins if p.is_dir()), os.environ.get("PATH", "")])
    import release_bootstrap

    release_bootstrap.start(_app_root)  # 背景準備 Lively / Ollama / ComfyUI，不延遲主視窗
    main()
