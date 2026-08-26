"""STT reaches the same conversation command entry as typed text."""

from types import SimpleNamespace

from ui.transparent_window import TransparentWindow


def test_transcript_submission_uses_action_bus_without_adapter_fallback():
    commands = []
    window = SimpleNamespace(
        _conversation_pending=False,
        _conversation_character_id=None,
        _action_bus=SimpleNamespace(execute=lambda command: commands.append(command) or SimpleNamespace(status="ok")),
        _set_agentic_busy=lambda _busy: None,
        set_action_status=lambda *_args, **_kwargs: None,
        get_current_character_id=lambda: "Choppr",
    )

    TransparentWindow.submit_agentic_text(window, "transcript")

    assert [(command.action, command.text, command.source, command.character_id) for command in commands] == [
        ("conversation", "transcript", "ui", "Choppr")
    ]


def test_second_transcript_cancels_previous_conversation_before_retrying():
    statuses = []
    window = SimpleNamespace(
        _conversation_pending=True,
        _conversation_character_id="Choppr",
        _action_bus=SimpleNamespace(execute=lambda _command: None),
        _set_agentic_busy=lambda _busy: None,
        set_action_status=lambda message, **_kwargs: statuses.append(message),
    )

    TransparentWindow.submit_agentic_text(window, "late transcript")

    assert statuses == ["No active character."]


def test_starting_new_recording_immediately_interrupts_active_conversation():
    calls = []
    motion = SimpleNamespace(has_active_motion=True)
    def interrupt_all():
        assert motion.has_active_motion is True
        calls.append("interrupt-all")
        motion.has_active_motion = False

    motion.interrupt_all = interrupt_all
    window = SimpleNamespace(
        _stt_available=True,
        _stt_state="idle",
        _conversation_pending=True,
        _conversation_character_id="Choppr",
        _conversation_trace_id="old-trace",
        _action_bus=SimpleNamespace(cancel_conversation=lambda: calls.append("cancel")),
        _motion_coordinator=motion,
        stop_motion_loop=lambda: calls.append("stop-motion"),
        restore_idle_video=lambda: calls.append("restore-idle"),
        stt_start_requested=SimpleNamespace(emit=lambda: calls.append("start-recording")),
    )

    TransparentWindow._handle_stt_button_clicked(window)

    assert calls == ["cancel", "interrupt-all", "stop-motion", "restore-idle", "start-recording"]
    assert window._conversation_pending is False
    assert window._conversation_trace_id is None


def test_starting_recording_interrupts_playback_after_conversation_has_finished():
    calls = []
    window = SimpleNamespace(
        _stt_available=True,
        _stt_state="idle",
        _conversation_pending=False,
        _conversation_trace_id=None,
        _action_bus=SimpleNamespace(cancel_conversation=lambda: calls.append("cancel")),
        _motion_coordinator=SimpleNamespace(interrupt_all=lambda: calls.append("interrupt-all")),
        stop_motion_loop=lambda: calls.append("stop-motion"),
        restore_idle_video=lambda: calls.append("restore-idle"),
        stt_start_requested=SimpleNamespace(emit=lambda: calls.append("start-recording")),
    )

    TransparentWindow._handle_stt_button_clicked(window)

    assert calls == ["cancel", "interrupt-all", "stop-motion", "restore-idle", "start-recording"]
