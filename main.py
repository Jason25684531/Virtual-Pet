"""
Application entrypoint for the ECHOES desktop host runtime.
"""

from __future__ import annotations

import signal
import sys


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


def _run_harness_mode(app):
    from interaction_trace import InteractionLatencyTracker
    from ui.transparent_window import TransparentWindow

    latency_tracker = InteractionLatencyTracker()
    window = TransparentWindow(brain_mode="harness", latency_tracker=latency_tracker)
    window.configure_runtime_context(
        runtime_contract=None,
        live_runtime_available=False,
    )
    window.show()
    window.set_action_status("Harness mode ready.", tone="idle", timeout_ms=2400)
    app.aboutToQuit.connect(window.shutdown_background_tasks)
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
