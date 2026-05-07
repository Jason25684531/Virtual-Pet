"""
ECHOES — 程式進入點
啟動 PyQt5 應用程式，顯示透明桌面寵物視窗。
"""

import sys
import signal
import time


WAVE_RESPONSE_GREETING_DIRECTIVE = "[ACTION:wave_response] hi~"


def build_wave_response_directive(_directive: str | None = None) -> str:
    return WAVE_RESPONSE_GREETING_DIRECTIVE


def connect_brain_output_handlers(window, brain_engine, sanitize_text):
    def handle_token_streamed(token: str, trace_id: str | None):
        append_message = getattr(window, "append_conversation_assistant", None)
        if not trace_id or not callable(append_message):
            return
        visible_text = sanitize_text(token)
        if visible_text:
            append_message(trace_id, visible_text)

    def handle_brain_fragment(fragment: str, trace_id: str | None):
        dispatch_action = getattr(window, "dispatch_action", None)
        if callable(dispatch_action):
            dispatch_action(fragment, trace_id=trace_id, allow_tts=False)

    def handle_sentence_ready(sentence_text: str, trace_id: str | None):
        speak_text = getattr(window, "speak_text", None)
        if callable(speak_text):
            speak_text(sentence_text, trace_id=trace_id)

    def handle_speech_ready(reply_text: str, trace_id: str | None):
        speak_text = getattr(window, "speak_text", None)
        if callable(speak_text):
            speak_text(reply_text, trace_id=trace_id)

    token_streamed = getattr(brain_engine, "token_streamed", None)
    if token_streamed is not None:
        token_streamed.connect(handle_token_streamed)
    brain_engine.streamed_fragment.connect(handle_brain_fragment)
    sentence_ready = getattr(brain_engine, "sentence_ready", None)
    if sentence_ready is not None:
        sentence_ready.connect(handle_sentence_ready)
    brain_engine.speech_ready.connect(handle_speech_ready)
    brain_completed = getattr(brain_engine, "brain_completed", None)
    if brain_completed is not None:
        brain_completed.connect(lambda trace_id: getattr(window, "complete_tts_trace", lambda *_args: None)(trace_id))


def main():
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from api_client.brain_engine import BrainEngine, sanitize_tts_text
    from api_client.voai_client import prewarm_voai_http_session
    import config
    from database import SQLiteMemoryManager
    from interaction_trace import InteractionLatencyTracker
    from interaction_turn_manager import InteractionTurnManager
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
    window.set_action_status("正在預熱 OpenAI 大腦...", tone="working", timeout_ms=2500)

    brain_engine = BrainEngine(latency_tracker=latency_tracker, parent=app)
    memory_manager = SQLiteMemoryManager()
    turn_manager = InteractionTurnManager(
        brain_engine,
        latency_tracker,
        prewarm_callback=lambda trace_id: prewarm_voai_http_session(
            trace_id=trace_id,
            latency_tracker=latency_tracker,
        ),
        parent=app,
    )
    stt_controller = STTSessionController(latency_tracker=latency_tracker, parent=app)
    original_apply_character = window.apply_character

    def apply_character_and_sync(character_id: str) -> bool:
        applied = original_apply_character(character_id)
        if applied:
            brain_engine.sync_profile_from_character(character_id=character_id)
        return applied

    window.apply_character = apply_character_and_sync  # type: ignore[method-assign]

    def handle_developer_query(text: str):
        preview = text if len(text) <= 24 else f"{text[:24]}..."
        result = turn_manager.submit("developer-input", text)
        if not result["accepted"]:
            window.set_action_status("Dev Query 送出失敗：請輸入非空白文字。", tone="warn", timeout_ms=3200)
            return
        if result["started"]:
            window.set_action_status(f"Dev Query 已送出: {preview}", tone="working", timeout_ms=0)
            return
        window.set_action_status(
            f"上一輪尚未完成，Dev Query 已加入佇列（待處理 {int(result['queue_position'])} 則）。",
            tone="working",
            timeout_ms=0,
        )

    window.developer_query_submitted.connect(handle_developer_query)

    wave_sensor_config = WaveDetectionConfig(
        detection_enabled=OPENCV_WAVE_DETECTION_ENABLED,
        show_debug_window=OPENCV_DEBUG_WINDOW_ENABLED,
    )
    wave_sensor = WaveSensor(config=wave_sensor_config, parent=app)
    window.set_stt_available(config.AZURE_STT_ENABLED)

    connect_brain_output_handlers(window, brain_engine, sanitize_tts_text)
    brain_engine.warning_emitted.connect(
        lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
    )
    brain_engine.start()

    def _source_label(source: str) -> str:
        if source == "stt":
            return "使用者語音"
        if source == "developer-input":
            return "Dev Query"
        return "使用者"

    def handle_turn_started(trace_id: str, source: str, text: str):
        preview = text if len(text) <= 40 else f"{text[:40]}..."
        window.begin_conversation_turn(trace_id, _source_label(source), text)
        window.set_action_status(f"正在回應：{preview}", tone="working", timeout_ms=0)

    def handle_turn_completed(trace_id: str, _source: str, _text: str):
        window.finish_conversation_turn(trace_id)
        if turn_manager.pending_count() > 0:
            window.set_action_status(
                f"本輪回應完成，下一輪待處理 {turn_manager.pending_count()} 則。",
                tone="working",
                timeout_ms=0,
            )
            return
        window.set_action_status("本輪互動完成。", tone="idle", timeout_ms=5200)

    turn_manager.turn_started.connect(handle_turn_started)
    turn_manager.turn_completed.connect(handle_turn_completed)
    turn_manager.queue_depth_changed.connect(window.set_conversation_queue_depth)

    def handle_stt_status(message: str):
        window.set_action_status(message, tone="working", timeout_ms=2400)

    def handle_stt_partial_preview(text: str):
        preview = text if len(text) <= 28 else f"{text[:28]}..."
        window.set_action_status(f"STT 辨識中: {preview}", tone="working", timeout_ms=1200)

    def handle_stt_warning(message: str):
        window.set_action_status(message, tone="warn", timeout_ms=4800)
        if not config.AZURE_STT_ENABLED:
            window.set_stt_available(False)

    def handle_stt_lifecycle_state(state: str):
        if not config.AZURE_STT_ENABLED:
            window.set_stt_available(False)
            return
        window.set_stt_state(state)
        if state == "listening":
            window.set_action_status("STT 收音中，等待語音輸入...", tone="working", timeout_ms=2200)
            return
        if state == "idle":
            window.set_action_status("STT 已停止收音", tone="idle", timeout_ms=2200)

    def handle_stt_preview(text: str, trace_id: str | None):
        preview = text if len(text) <= 24 else f"{text[:24]}..."
        result = turn_manager.submit("stt", text, trace_id=trace_id)
        if not result["accepted"]:
            window.set_action_status("STT 文字送出失敗。", tone="warn", timeout_ms=2800)
            return
        queued_trace_id = result["trace_id"]
        if queued_trace_id:
            print(f"[ECHOES][STT] 將辨識文字送入 BrainEngine: {preview} | trace={queued_trace_id}")
        if result["started"]:
            window.set_action_status(f"STT 已送出: {preview}", tone="working", timeout_ms=0)
            return
        print(f"[ECHOES][STT] 已排入互動佇列: {preview} | waiting={int(result['queue_position'])}")
        window.set_action_status(
            f"上一輪回應中，已排入新句子（待處理 {int(result['queue_position'])} 則）。",
            tone="working",
            timeout_ms=0,
        )

    stt_controller.status_changed.connect(handle_stt_status)
    stt_controller.recognizing_text.connect(handle_stt_partial_preview)
    stt_controller.warning_emitted.connect(handle_stt_warning)
    stt_controller.session_lifecycle_changed.connect(handle_stt_lifecycle_state)
    stt_controller.recognized_result.connect(handle_stt_preview)
    window.stt_start_requested.connect(stt_controller.start_session)
    window.stt_stop_requested.connect(stt_controller.stop_session)

    def handle_reset_requested():
        stt_controller.stop_session()
        turn_manager.reset()
        current_character_id = window.get_current_character_id()
        memory_manager.clear_session(current_character_id)
        brain_engine.clear_memory()
        window.reset_runtime_state()
        if config.AZURE_STT_ENABLED and stt_controller.state() not in {"starting", "listening", "stopping"}:
            window.set_stt_state("idle")
        elif not config.AZURE_STT_ENABLED:
            window.set_stt_available(False)

    window.reset_requested.connect(handle_reset_requested)

    if not config.AZURE_STT_ENABLED:
        print("[ECHOES][STT] 提示: Azure STT 設定尚未完成；收音按鈕會顯示為不可用。")

    WAVE_RESPONSE_COOLDOWN_S = 8.0  # 揮手動作結束後的冷卻秒數（兩次 dispatch 最短間隔）
    _last_wave_time = float("-inf")

    def _on_wave_detected(directive: str):
        nonlocal _last_wave_time
        now = time.monotonic()
        elapsed = now - _last_wave_time
        if elapsed < WAVE_RESPONSE_COOLDOWN_S:
            remaining = WAVE_RESPONSE_COOLDOWN_S - elapsed
            print(f"[ECHOES] Wave response 略過：冷卻中（剩餘 {remaining:.1f}s）")
            return
        if window.is_busy:
            print("[ECHOES] Wave response 略過：STT 或 TTS 進行中")
            return
        _last_wave_time = now  # 只在實際 dispatch 時更新計時器
        window.dispatch_action(build_wave_response_directive(directive))

    if wave_sensor_config.detection_enabled:
        wave_sensor.wave_detected.connect(_on_wave_detected)
        wave_sensor.sensor_warning.connect(
            lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
        )
        wave_sensor.start()
        if wave_sensor_config.show_debug_window:
            print("[ECHOES] 提示: OpenCV 偵測預覽視窗已啟用。")
    else:
        print("[ECHOES] 提示: OpenCV 揮手偵測已關閉，可到 sensors/camera_vision.py 將 boolean 改為 True。")

    def shutdown_brain_engine():
        turn_manager.shutdown()
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

    def shutdown_window_workers():
        window.shutdown_background_tasks()

    app.aboutToQuit.connect(shutdown_brain_engine)
    app.aboutToQuit.connect(shutdown_wave_sensor)
    app.aboutToQuit.connect(shutdown_stt_worker)
    app.aboutToQuit.connect(shutdown_window_workers)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
