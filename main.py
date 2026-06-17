"""
Application entrypoint for the ECHOES desktop host runtime.
"""

from __future__ import annotations

import argparse
import re
import signal
import sys
import time

from brain_mode import resolve_brain_mode

WAVE_RESPONSE_GREETING_DIRECTIVE = "[ACTION:wave_response] 嗨 你好嗎"
DEFAULT_REPLY_ACTION_DIRECTIVE = "[ACTION:listen]"
ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


def build_wave_response_directive(_directive: str | None = None) -> str:
    return WAVE_RESPONSE_GREETING_DIRECTIVE


def resolve_cached_intent_from_text(text: str) -> str | None:
    from action_services import resolve_fixed_intent_from_text

    return resolve_fixed_intent_from_text(text)


def build_cached_intent_trigger_source(intent_name: str, source_kind: str) -> str:
    from action_services import FIXED_INTENT_LABELS

    label = FIXED_INTENT_LABELS.get(str(intent_name or "").strip().lower(), intent_name)
    return f"{label} {source_kind}".strip()


def build_stt_cached_intent_trigger_source(text: str, intent_name: str) -> str:
    normalized_text = str(text or "").strip()
    if normalized_text:
        return normalized_text
    return build_cached_intent_trigger_source(intent_name, "關鍵字觸發")


def connect_brain_output_handlers(window, brain_engine, sanitize_text):
    from action_services import FIXED_NEWS_SCRIPT

    reply_action_state: dict[str, dict[str, bool]] = {}

    def _normalize_trace_id(trace_id: str | None) -> str:
        return str(trace_id or "").strip()

    def _get_or_create_trace_state(trace_id: str | None) -> dict[str, bool] | None:
        normalized_trace_id = _normalize_trace_id(trace_id)
        if not normalized_trace_id:
            return None
        return reply_action_state.setdefault(
            normalized_trace_id,
            {
                "saw_explicit_action": False,
                "injected_default_action": False,
                "uses_fixed_news_reply": False,
            },
        )

    def _extract_explicit_action(text: str) -> str | None:
        match = ACTION_DIRECTIVE_PATTERN.search(str(text or ""))
        if not match:
            return None
        action_name = (match.group("bracket") or match.group("bare") or "").strip().lower()
        if not action_name:
            return None
        try:
            import config

            return config.canonicalize_host_action(action_name) or action_name
        except Exception:
            return action_name

    def _activate_fixed_news_reply(trace_id: str | None):
        state = _get_or_create_trace_state(trace_id)
        if state is None or state["uses_fixed_news_reply"]:
            return
        state["uses_fixed_news_reply"] = True
        set_assistant = getattr(window, "set_conversation_assistant", None)
        if callable(set_assistant):
            set_assistant(trace_id, FIXED_NEWS_SCRIPT)

    def _maybe_inject_default_reply_action(trace_id: str | None):
        dispatch_action = getattr(window, "dispatch_action", None)
        if not callable(dispatch_action):
            return
        state = _get_or_create_trace_state(trace_id)
        if state is None:
            return
        if state["saw_explicit_action"] or state["injected_default_action"]:
            return
        dispatch_action(DEFAULT_REPLY_ACTION_DIRECTIVE, trace_id=trace_id, allow_tts=False)
        state["injected_default_action"] = True

    def _clear_reply_action_state(trace_id: str | None):
        normalized_trace_id = _normalize_trace_id(trace_id)
        if normalized_trace_id:
            reply_action_state.pop(normalized_trace_id, None)

    def handle_token_streamed(token: str, trace_id: str | None):
        append_message = getattr(window, "append_conversation_assistant", None)
        if not trace_id or not callable(append_message):
            return
        state = _get_or_create_trace_state(trace_id)
        if state is not None and state["uses_fixed_news_reply"]:
            return
        visible_text = sanitize_text(token)
        if visible_text:
            append_message(trace_id, visible_text)

    def handle_brain_fragment(fragment: str, trace_id: str | None):
        state = _get_or_create_trace_state(trace_id)
        explicit_action = _extract_explicit_action(fragment)
        if explicit_action:
            if state is not None:
                state["saw_explicit_action"] = True
            if explicit_action == "report_news":
                _activate_fixed_news_reply(trace_id)
        else:
            _maybe_inject_default_reply_action(trace_id)
        dispatch_action = getattr(window, "dispatch_action", None)
        if callable(dispatch_action):
            dispatch_action(fragment, trace_id=trace_id, allow_tts=False)

    def handle_sentence_ready(sentence_text: str, trace_id: str | None):
        speak_text = getattr(window, "speak_text", None)
        if callable(speak_text):
            speak_text(sentence_text, trace_id=trace_id)

    def handle_speech_ready(reply_text: str, trace_id: str | None):
        _maybe_inject_default_reply_action(trace_id)
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
        brain_completed.connect(
            lambda trace_id: (
                _clear_reply_action_state(trace_id),
                getattr(window, "complete_tts_trace", lambda *_args: None)(trace_id),
            )
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ECHOES desktop pet host",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--brain-mode",
        dest="brain_mode",
        default=None,
        metavar="MODE",
        help=(
            "brain mode (harness|openclaw|auto).\n"
            "  harness  - use the local harness validation UI only\n"
            "  openclaw - keep the legacy runtime path enabled\n"
            "  auto     - currently follows the legacy runtime path\n"
            "CLI overrides ECHOES_BRAIN_MODE."
        ),
    )
    return parser.parse_args()


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


def _run_harness_mode(app, brain_mode: str):
    from interaction_trace import InteractionLatencyTracker
    from ui.transparent_window import TransparentWindow

    latency_tracker = InteractionLatencyTracker()
    window = TransparentWindow(brain_mode=brain_mode, latency_tracker=latency_tracker)
    window.show()
    window.set_action_status("Harness mode ready.", tone="idle", timeout_ms=2400)
    app.aboutToQuit.connect(window.shutdown_background_tasks)
    return window


def _run_legacy_runtime(app, brain_mode: str):
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

    latency_tracker = InteractionLatencyTracker()
    window = TransparentWindow(brain_mode=brain_mode, latency_tracker=latency_tracker)
    window.show()
    window.set_action_status("Starting LangchainDev runtime...", tone="working", timeout_ms=2500)

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
        result = turn_manager.submit("developer-input", text)
        if not result["accepted"]:
            window.set_action_status("Developer query busy.", tone="warn", timeout_ms=3200)
            return
        if result["started"]:
            window.set_action_status("Developer query sent.", tone="working", timeout_ms=0)
            return
        window.set_action_status(
            f"Developer query queued: {int(result['queue_position'])}",
            tone="working",
            timeout_ms=0,
        )

    window.developer_query_submitted.connect(handle_developer_query)

    def handle_cached_intent_request(intent_name: str, trigger_source: str):
        if not window.trigger_cached_intent(intent_name, trigger_source):
            return
        window.set_action_status(f"{trigger_source} queued...", tone="working", timeout_ms=0)

    window.cached_intent_requested.connect(handle_cached_intent_request)

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
            return "STT"
        if source == "developer-input":
            return "Dev Query"
        return "User"

    def handle_turn_started(trace_id: str, source: str, text: str):
        window.begin_conversation_turn(trace_id, _source_label(source), text)
        window.set_action_status("Thinking...", tone="working", timeout_ms=0)

    def handle_turn_completed(trace_id: str, _source: str, _text: str):
        window.finish_conversation_turn(trace_id)
        pending = turn_manager.pending_count()
        if pending > 0:
            window.set_action_status(f"Queued turns remaining: {pending}", tone="working", timeout_ms=0)
            return
        window.set_action_status("Turn complete.", tone="idle", timeout_ms=5200)

    turn_manager.turn_started.connect(handle_turn_started)
    turn_manager.turn_completed.connect(handle_turn_completed)
    turn_manager.queue_depth_changed.connect(window.set_conversation_queue_depth)

    def handle_stt_status(message: str):
        window.set_action_status(message, tone="working", timeout_ms=2400)

    def handle_stt_partial_preview(text: str):
        preview = text if len(text) <= 28 else f"{text[:28]}..."
        window.set_action_status(f"STT partial: {preview}", tone="working", timeout_ms=1200)

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
            window.set_action_status("STT listening...", tone="working", timeout_ms=2200)
        elif state == "idle":
            window.set_action_status("STT idle.", tone="idle", timeout_ms=2200)

    def handle_stt_preview(text: str, trace_id: str | None):
        preview = text if len(text) <= 24 else f"{text[:24]}..."
        fixed_intent = resolve_cached_intent_from_text(text)
        if fixed_intent:
            handle_cached_intent_request(
                fixed_intent,
                build_stt_cached_intent_trigger_source(text, fixed_intent),
            )
            return
        result = turn_manager.submit("stt", text, trace_id=trace_id)
        if not result["accepted"]:
            window.set_action_status("STT request rejected.", tone="warn", timeout_ms=2800)
            return
        if result["started"]:
            window.set_action_status(f"STT sent: {preview}", tone="working", timeout_ms=0)
            return
        window.set_action_status(
            f"STT queued: {int(result['queue_position'])}",
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

    wave_cooldown_s = 8.0
    last_wave_time = float("-inf")

    def _on_wave_detected(directive: str):
        nonlocal last_wave_time
        now = time.monotonic()
        if now - last_wave_time < wave_cooldown_s:
            return
        if window.is_busy:
            return
        last_wave_time = now
        window.dispatch_action(build_wave_response_directive(directive))

    if wave_sensor_config.detection_enabled:
        wave_sensor.wave_detected.connect(_on_wave_detected)
        wave_sensor.sensor_warning.connect(
            lambda message: window.set_action_status(message, tone="warn", timeout_ms=4800)
        )
        wave_sensor.start()

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

    return window


def main():
    args = _parse_args()
    brain_mode = resolve_brain_mode(args.brain_mode)
    print(f"[ECHOES] brain mode: {brain_mode}")

    app = _create_application(sys.argv)
    _configure_sigint_timer(app)

    if brain_mode == "harness":
        _run_harness_mode(app, brain_mode)
    else:
        _run_legacy_runtime(app, brain_mode)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
