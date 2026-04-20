"""
ECHOES — 程式進入點
啟動 PyQt5 應用程式，顯示透明桌面寵物視窗。
"""

import sys
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
    from api_client.vm_connector import VMConnector
    from sensors.camera_vision import WaveSensor
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
    window.set_action_status("正在連線 OpenClaw 大腦...", tone="working", timeout_ms=2500)

    vm_connector = VMConnector(parent=app)
    wave_sensor = WaveSensor(parent=app)

    vm_connector.message_received.connect(window.dispatch_action)
    vm_connector.start()
    wave_sensor.wave_detected.connect(window.dispatch_action)
    wave_sensor.sensor_warning.connect(
        lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
    )
    wave_sensor.start()

    def shutdown_vm_connector():
        vm_connector.stop()
        if vm_connector.isRunning():
            vm_connector.wait(3000)

    def shutdown_wave_sensor():
        wave_sensor.stop()
        if wave_sensor.isRunning():
            wave_sensor.wait(3000)

    app.aboutToQuit.connect(shutdown_vm_connector)
    app.aboutToQuit.connect(shutdown_wave_sensor)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
