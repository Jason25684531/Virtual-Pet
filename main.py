"""
ECHOES — 程式進入點
啟動 PyQt5 應用程式，顯示透明桌面寵物視窗。
"""

import sys
import os
import signal

# 情緒 → WebM 檔名對應表（供未來 VM/Sensor 模組使用）
EMOTION_MAP = {
    "開心": "laugh.webm",
    "生氣": "angry.webm",
    "尷尬": "awkward.webm",
    "無言": "speechless.webm",
    "聆聽": "listen.webm",
    "預設": "idle.webm",
}


def main():
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

    # 測試用：確認房間狀態區可以從 Python 更新
    def test_room_status():
        print("[ECHOES] 測試: 更新房間狀態文字")
        window.set_action_status("房間模式橋接正常", tone="idle", timeout_ms=2500)

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(test_room_status)
    timer.start(5000)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
