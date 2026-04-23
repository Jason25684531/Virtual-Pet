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
    import config
    from interaction_trace import InteractionLatencyTracker
    from sensors.camera_vision import (
        OPENCV_DEBUG_WINDOW_ENABLED,
        OPENCV_WAVE_DETECTION_ENABLED,
        WaveDetectionConfig,
        WaveSensor,
    )
    from sensors.stt_session_controller import STTSessionController
    from ui.transparent_window import TransparentWindow

    app = QApplication(sys.argv)

    # 讓 Ctrl+C 可以正常終止程序
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    # 每 200ms 讓 Python 處理一次訊號（PyQt 事件迴圈不會主動讓出 CPU 給 Python）
    sigint_timer = QTimer()
    sigint_timer.start(200)
    sigint_timer.timeout.connect(lambda: None)

    latency_tracker = InteractionLatencyTracker()
    window = TransparentWindow(latency_tracker=latency_tracker)
    window.show()
    window.set_action_status("正在預熱本地 Ollama 大腦...", tone="working", timeout_ms=2500)

    brain_engine = BrainEngine(latency_tracker=latency_tracker, parent=app)
    stt_controller = STTSessionController(parent=app)
    original_apply_character = window.apply_character

    def apply_character_and_sync(character_id: str) -> bool:
        applied = original_apply_character(character_id)
        if applied:
            brain_engine.sync_profile_from_character(character_id=character_id)
        return applied

    window.apply_character = apply_character_and_sync  # type: ignore[method-assign]

    def handle_developer_query(text: str):
        preview = text if len(text) <= 24 else f"{text[:24]}..."
        trace_id = latency_tracker.begin_interaction("developer-input", text)
        window.set_action_status(f"Dev Query 已送出: {preview}", tone="working", timeout_ms=2800)
        if not brain_engine.send_query(text, trace_id=trace_id):
            latency_tracker.abort(trace_id, "developer query 未送入 BrainEngine")
            window.set_action_status("Dev Query 送出失敗：請輸入非空白文字。", tone="warn", timeout_ms=3200)

    window.developer_query_submitted.connect(handle_developer_query)

    wave_sensor_config = WaveDetectionConfig(
        detection_enabled=OPENCV_WAVE_DETECTION_ENABLED,
        show_debug_window=OPENCV_DEBUG_WINDOW_ENABLED,
    )
    wave_sensor = WaveSensor(config=wave_sensor_config, parent=app)
    window.set_stt_available(config.AZURE_STT_ENABLED)

    def handle_brain_fragment(fragment: str, trace_id: str | None):
        window.dispatch_action(fragment, trace_id=trace_id)

    brain_engine.streamed_fragment.connect(handle_brain_fragment)
    brain_engine.warning_emitted.connect(
        lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
    )
    brain_engine.start()

    def handle_stt_status(message: str):
        window.set_action_status(message, tone="working", timeout_ms=2400)

    def handle_stt_warning(message: str):
        window.set_action_status(message, tone="warn", timeout_ms=4800)
        if not config.AZURE_STT_ENABLED:
            window.set_stt_available(False)

    def handle_stt_session_state(active: bool):
        window.set_stt_listening(active)
        if active:
            window.set_action_status("STT 收音中，等待語音輸入...", tone="working", timeout_ms=2200)
            return
        window.set_action_status("STT 已停止收音", tone="idle", timeout_ms=2200)

    def handle_stt_preview(text: str):
        trace_id = latency_tracker.begin_interaction("stt", text)
        preview = text if len(text) <= 24 else f"{text[:24]}..."
        print(f"[ECHOES][STT] 將辨識文字送入 BrainEngine: {preview} | trace={trace_id}")
        window.set_action_status(f"STT 已送出: {preview}", tone="working", timeout_ms=2800)
        if not brain_engine.send_to_brain(text, trace_id=trace_id):
            latency_tracker.abort(trace_id, "STT 文字未送入 BrainEngine")
            window.set_action_status("STT 文字送出失敗。", tone="warn", timeout_ms=2800)

    stt_controller.status_changed.connect(handle_stt_status)
    stt_controller.warning_emitted.connect(handle_stt_warning)
    stt_controller.session_state_changed.connect(handle_stt_session_state)
    stt_controller.recognized_text.connect(handle_stt_preview)
    window.stt_start_requested.connect(stt_controller.start_session)
    window.stt_stop_requested.connect(stt_controller.stop_session)

    if not config.AZURE_STT_ENABLED:
        print("[ECHOES][STT] 提示: Azure STT 設定尚未完成；收音按鈕會顯示為不可用。")

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

    def shutdown_stt_worker():
        stt_controller.shutdown()

    app.aboutToQuit.connect(shutdown_brain_engine)
    app.aboutToQuit.connect(shutdown_wave_sensor)
    app.aboutToQuit.connect(shutdown_stt_worker)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
