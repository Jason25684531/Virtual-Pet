"""STT reaches the same conversation command entry as typed text."""

from types import SimpleNamespace

from ui.transparent_window import TransparentWindow


def test_transcript_submission_uses_action_bus_without_adapter_fallback():
    commands = []
    window = SimpleNamespace(
        _conversation_pending=False,
        _action_bus=SimpleNamespace(execute=lambda command: commands.append(command) or SimpleNamespace(status="ok")),
        _set_agentic_busy=lambda _busy: None,
        set_action_status=lambda *_args, **_kwargs: None,
    )

    TransparentWindow.submit_agentic_text(window, "transcript")

    assert [(command.action, command.text, command.source) for command in commands] == [
        ("conversation", "transcript", "ui")
    ]


def test_second_transcript_is_rejected_while_conversation_is_pending():
    statuses = []
    window = SimpleNamespace(
        _conversation_pending=True,
        _action_bus=SimpleNamespace(execute=lambda _command: None),
        _set_agentic_busy=lambda _busy: None,
        set_action_status=lambda message, **_kwargs: statuses.append(message),
    )

    TransparentWindow.submit_agentic_text(window, "late transcript")

    assert statuses == ["Interaction already running."]
