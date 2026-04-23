"""
ECHOES — 程式進入點
啟動 PyQt5 應用程式，顯示透明桌面寵物視窗。
"""

import sys
import signal


def main():
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from api_client.brain_engine import BrainEngine
    from sensors.camera_vision import (
        OPENCV_DEBUG_WINDOW_ENABLED,
        OPENCV_WAVE_DETECTION_ENABLED,
        WaveDetectionConfig,
        WaveSensor,
    )
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
    window.set_action_status("正在預熱本地 Ollama 大腦...", tone="working", timeout_ms=2500)

    brain_engine = BrainEngine(parent=app)
    original_apply_character = window.apply_character

    def apply_character_and_sync(character_id: str) -> bool:
        applied = original_apply_character(character_id)
        if applied:
            brain_engine.sync_profile_from_character(character_id=character_id)
        return applied

    window.apply_character = apply_character_and_sync  # type: ignore[method-assign]

    def handle_developer_query(text: str):
        preview = text if len(text) <= 24 else f"{text[:24]}..."
        window.set_action_status(f"Dev Query 已送出: {preview}", tone="working", timeout_ms=2800)
        if not brain_engine.send_query(text):
            window.set_action_status("Dev Query 送出失敗：請輸入非空白文字。", tone="warn", timeout_ms=3200)

    window.developer_query_submitted.connect(handle_developer_query)

    wave_sensor_config = WaveDetectionConfig(
        detection_enabled=OPENCV_WAVE_DETECTION_ENABLED,
        show_debug_window=OPENCV_DEBUG_WINDOW_ENABLED,
    )
    wave_sensor = WaveSensor(config=wave_sensor_config, parent=app)

    brain_engine.message_received.connect(window.dispatch_action)
    brain_engine.warning_emitted.connect(
        lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
    )
    brain_engine.start()
    if wave_sensor_config.detection_enabled:
        wave_sensor.wave_detected.connect(window.dispatch_action)
        wave_sensor.sensor_warning.connect(
            lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
        )
        wave_sensor.start()
        if wave_sensor_config.show_debug_window:
            print("[ECHOES] 提示: OpenCV 偵測預覽視窗已啟用。")
    else:
        print("[ECHOES] 提示: OpenCV 揮手偵測已關閉，可到 sensors/camera_vision.py 將 boolean 改為 True。")

    def shutdown_brain_engine():
        brain_engine.stop()
        brain_engine.quit()
        if brain_engine.isRunning():
            brain_engine.wait(3000)

    def shutdown_wave_sensor():
        if not wave_sensor_config.detection_enabled:
            return
        wave_sensor.stop()
        wave_sensor.quit()
        if wave_sensor.isRunning():
            wave_sensor.wait(3000)

    app.aboutToQuit.connect(shutdown_brain_engine)
    app.aboutToQuit.connect(shutdown_wave_sensor)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
