"""
ECHOES — 程式進入點
啟動 PyQt5 應用程式，顯示透明桌面寵物視窗。
"""

import sys
import os
import signal


def main():
    # Chromium 透明渲染所需的命令列參數
    sys.argv += ["--disable-gpu"]

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from ui.transparent_window import TransparentWindow

    app = QApplication(sys.argv)

    # 讓 Ctrl+C 可以正常終止程序
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    # 每 200ms 讓 Python 處理一次訊號（PyQt 事件迴圈不會主動讓出 CPU 給 Python）
    sigint_timer = QTimer()
    sigint_timer.start(200)
    sigint_timer.timeout.connect(lambda: None)

    window = TransparentWindow()
    window.show()

    # 測試用：5 秒後呼叫 JS changeVideo 驗證 Python→JS 通路
    def test_change_video():
        print("[ECHOES] 測試: 呼叫 changeVideo('idle.webm')")
        window.change_video("idle.webm")

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(test_change_video)
    timer.start(5000)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
