"""Entry points hand off to the window's application command methods."""

from types import SimpleNamespace

from ui.transparent_window import TransparentWindow


def test_quick_intent_bridge_and_overlay_aliases_reach_their_command_entries():
    quick_calls, skill_calls = [], []
    window = SimpleNamespace(
        trigger_cached_intent=lambda name, source: quick_calls.append((name, source)),
        trigger_enabled_skill_for_behavior=lambda behavior: skill_calls.append(behavior),
    )

    TransparentWindow.trigger_quick_intent_from_bridge(window, "joke")
    TransparentWindow.trigger_overlay_action_from_bridge(window, "music")
    TransparentWindow.trigger_overlay_action_from_bridge(window, "news")

    assert quick_calls == [("joke", "joke 面板觸發")]
    # 技能定義的 behavior 欄位一律是 music_idle/news_idle（見 .agentic/skills/*.md），
    # play_music/report_news 是 action_dispatcher 的一次性動作播放鍵，命名空間不同、
    # 從未有技能以此為 behavior，比對永遠落空。
    assert skill_calls == ["music_idle", "news_idle"]


def test_submit_agentic_text_sends_a_conversation_command_to_action_bus():
    commands, conversation_calls = [], []
    window = SimpleNamespace(
        _action_bus=SimpleNamespace(execute=lambda command: commands.append(command) or SimpleNamespace(status="ok")),
        _conversation_pending=False,
        _conversation_character_id=None,
        _proactive_greeting_active=False,
        _proactive_greeting_release_timer=SimpleNamespace(stop=lambda: None, start=lambda: None),
        _greeter=SimpleNamespace(reset=lambda: None, start=lambda: None, stop=lambda: None),
        _motion_coordinator=None,
        _set_agentic_busy=lambda active: None,
        set_action_status=lambda *args, **kwargs: None,
        get_current_character_id=lambda: "Choppr",
        begin_conversation_turn=lambda *args: conversation_calls.append(args),
    )

    TransparentWindow.submit_agentic_text(window, "hello")

    assert [(command.action, command.text, command.source, command.character_id) for command in commands] == [
        ("conversation", "hello", "ui", "Choppr")
    ]
    assert conversation_calls == [(commands[0].trace_id, "Talk", "hello")]


def test_rejected_conversation_closes_waiting_turn_with_failure():
    calls = []
    stream_finishes = []
    window = SimpleNamespace(
        _action_bus=SimpleNamespace(
            execute=lambda _command: SimpleNamespace(status="rejected", reason="busy")
        ),
        _conversation_pending=False,
        _conversation_character_id=None,
        _conversation_trace_id=None,
        _proactive_greeting_active=False,
        _proactive_greeting_release_timer=SimpleNamespace(stop=lambda: None, start=lambda: None),
        _greeter=SimpleNamespace(reset=lambda: None, start=lambda: None, stop=lambda: None),
        _motion_coordinator=SimpleNamespace(
            finish_streaming_trace=lambda trace_id: stream_finishes.append(trace_id),
        ),
        _set_agentic_busy=lambda active: None,
        set_action_status=lambda *args, **kwargs: None,
        get_current_character_id=lambda: "Choppr",
        begin_conversation_turn=lambda *args: calls.append(("begin", args)),
        set_conversation_assistant=lambda *args: calls.append(("assistant", args)),
        finish_conversation_turn=lambda *args: calls.append(("finish", args)),
    )

    TransparentWindow.submit_agentic_text(window, "hello")

    trace_id = calls[0][1][0]
    assert calls == [
        ("begin", (trace_id, "Talk", "hello")),
        ("assistant", (trace_id, "busy")),
        ("finish", (trace_id,)),
    ]
    assert stream_finishes == [trace_id]


def test_action_bus_error_closes_waiting_streaming_trace():
    stream_finishes = []
    window = SimpleNamespace(
        _motion_coordinator=SimpleNamespace(
            finish_streaming_trace=lambda trace_id: stream_finishes.append(trace_id),
        ),
        _conversation_trace_id="trace-1",
        _conversation_character_id="Choppr",
        _conversation_pending=True,
        _set_agentic_busy=lambda _busy: None,
        get_current_character_id=lambda: "Choppr",
        _is_current_conversation_character=lambda character_id: character_id == "Choppr",
        set_conversation_assistant=lambda *_args: None,
        finish_conversation_turn=lambda *_args: None,
        _finish_conversation_for=lambda _character_id: None,
        _on_agentic_error=lambda _message: None,
    )

    TransparentWindow._on_action_bus_error(window, "provider failed", "Choppr")

    assert stream_finishes == ["trace-1"]


def test_submit_agentic_text_interrupts_finished_turns_with_active_tts_motion():
    calls, commands = [], []
    window = SimpleNamespace(
        _action_bus=SimpleNamespace(
            cancel_conversation=lambda: calls.append("cancel"),
            execute=lambda command: commands.append(command) or SimpleNamespace(status="ok"),
        ),
        _motion_coordinator=SimpleNamespace(
            has_active_motion=True, is_tts_busy=True, interrupt_all=lambda: calls.append("interrupt"),
        ),
        _conversation_pending=False,
        _conversation_character_id=None,
        _conversation_trace_id=None,
        _proactive_greeting_active=False,
        _proactive_greeting_release_timer=SimpleNamespace(stop=lambda: None, start=lambda: None),
        _greeter=SimpleNamespace(reset=lambda: None, start=lambda: None, stop=lambda: None),
        _set_agentic_busy=lambda active: calls.append(("busy", active)),
        set_action_status=lambda *args, **kwargs: None,
        stop_motion_loop=lambda: calls.append("stop-motion"),
        restore_idle_video=lambda: calls.append("restore-idle"),
        get_current_character_id=lambda: "Choppr",
        begin_conversation_turn=lambda *args: None,
    )

    TransparentWindow.submit_agentic_text(window, "next turn")

    assert calls[:4] == ["cancel", "interrupt", "stop-motion", "restore-idle"]
    assert commands[0].text == "next turn"


def test_streaming_result_dispatches_its_final_action_when_no_stream_action_started():
    calls = []
    window = SimpleNamespace(
        _latest_agentic_event=None,
        _latency_tracker=None,
        _motion_coordinator=None,
        _validated_event_motion_key=lambda _payload: "laugh",
        begin_conversation_turn=lambda *args: calls.append(("begin", args)),
        set_conversation_assistant=lambda *args: calls.append(("reply", args)),
        finish_conversation_turn=lambda *args: calls.append(("finish", args)),
        dispatch_action=lambda *args, **kwargs: calls.append(("action", args, kwargs)),
        speak_text=lambda *args, **kwargs: calls.append(("speak", args, kwargs)),
        refresh_agentic_ui=lambda **kwargs: calls.append(("refresh", kwargs)),
    )

    TransparentWindow.consume_interaction_result(
        window,
        {"reply": "hello", "metadata": {"agentic": {"streaming": True}}, "webm_key": "laugh"},
    )

    assert [call for call in calls if call[0] == "action"] == [
        ("action", ("[ACTION:laugh]",), {"trace_id": calls[0][1][0], "allow_tts": True, "wait_for_tts_start": True})
    ]
