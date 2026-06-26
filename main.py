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
    from PyQt5.QtCore import QCoreApplication, Qt
    from PyQt5.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
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


def main():
    print("[ECHOES] brain mode: harness")

    app = _create_application(sys.argv)
    _configure_sigint_timer(app)
    _run_harness_mode(app)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
