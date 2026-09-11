"""被中斷的回合與主動發話都必須留在對話歷史裡（specs/conversation-continuity）。

recent_events 是 prompt 短期歷史的唯一來源；掉一筆就等於模型失憶。
"""

from unittest.mock import MagicMock

from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.events import UserEvent
from pet_harness.storage.sqlite_store import SQLiteStore
from ui.transparent_window import TransparentWindow


def _store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    return store


def _logged_texts(store: SQLiteStore) -> list[str]:
    return [event["input_payload"].get("text", "") for event in store.recent_events(limit=20)]


def _logged_replies(store: SQLiteStore) -> list[str]:
    return [event["output_payload"].get("reply", "") for event in store.recent_events(limit=20)]


# ── 主動發話入帳（#14 剩餘缺口）──────────────────────────────


def test_assistant_utterance_is_logged_to_history(tmp_path):
    store = _store(tmp_path)
    engine = MagicMock(store=store, _spoken_chunks=[])

    PetHarnessEngine.log_assistant_utterance(engine, "想和我聊聊嗎？")

    assert "想和我聊聊嗎？" in _logged_replies(store)


def test_assistant_utterance_awards_no_xp_and_runs_no_triggers(tmp_path):
    store = _store(tmp_path)
    engine = MagicMock(store=store)

    PetHarnessEngine.log_assistant_utterance(engine, "我在這裡陪你喔！")

    assert store.get_user_progress()["xp_total"] == 0
    assert store.get_setting("asset_pending_offer") is None
    engine.xp_manager.award_for_event.assert_not_called()


def test_assistant_utterance_has_no_user_text(tmp_path):
    """user 欄位留空，才不會被當成使用者說過的話。"""
    store = _store(tmp_path)
    engine = MagicMock(store=store)

    PetHarnessEngine.log_assistant_utterance(engine, "今天也一起加油吧！")

    assert _logged_texts(store) == [""]


def test_blank_assistant_utterance_is_ignored(tmp_path):
    store = _store(tmp_path)
    engine = MagicMock(store=store)

    PetHarnessEngine.log_assistant_utterance(engine, "   ")

    assert store.recent_events(limit=5) == []


def test_greeting_path_records_the_utterance():
    fake = MagicMock()
    fake._proactive_greeting_active = False
    fake.dispatch_action.return_value = True

    TransparentWindow._speak_proactive_greeting(fake, "想和我聊聊嗎？")

    fake._log_assistant_utterance.assert_called_once_with("想和我聊聊嗎？")


def test_greeting_survives_a_history_write_failure():
    """記錄失敗不能讓打招呼本身壞掉。"""
    fake = MagicMock()
    fake._adapter.engine.log_assistant_utterance.side_effect = RuntimeError("db locked")

    TransparentWindow._log_assistant_utterance(fake, "嗨")

    fake._adapter.engine.log_assistant_utterance.assert_called_once()


def test_utterance_logging_is_skipped_when_no_engine_is_bound():
    fake = MagicMock()
    fake._adapter.engine = None

    TransparentWindow._log_assistant_utterance(fake, "嗨")  # 不應拋出


# ── 語音片段不跨 trace 污染 engine ─────────────────────────────


def test_spoken_chunks_from_the_active_turn_reach_the_engine():
    fake = MagicMock()
    fake._conversation_trace_id = "turn-1"
    fake._spoken_chunks = {}

    TransparentWindow.record_spoken_chunk(fake, "turn-1", "今天天氣不錯")

    fake._adapter.engine.mark_spoken_chunk.assert_called_once_with("今天天氣不錯")


def test_greeting_chunks_do_not_leak_into_the_engine():
    """打招呼累進 engine._spoken_chunks 後，下一個回合若在產出內容前被取消，
    spoken_reply() 會把打招呼的句子當成該回合的 reply。"""
    fake = MagicMock()
    fake._conversation_trace_id = "turn-1"
    fake._spoken_chunks = {}

    TransparentWindow.record_spoken_chunk(fake, "greeting-abc", "想和我聊聊嗎？")

    fake._adapter.engine.mark_spoken_chunk.assert_not_called()
    # 但 UI 自己的紀錄仍然保留
    assert fake._spoken_chunks["greeting-abc"] == ["想和我聊聊嗎？"]


def test_chunks_from_a_stale_turn_do_not_reach_the_engine():
    fake = MagicMock()
    fake._conversation_trace_id = "turn-2"
    fake._spoken_chunks = {}

    TransparentWindow.record_spoken_chunk(fake, "turn-1", "上一輪的殘留")

    fake._adapter.engine.mark_spoken_chunk.assert_not_called()


# ── 切換角色與離開舞台的中斷（#1 #7）──────────────────────────


def _staged_window():
    fake = MagicMock()
    fake._stage_active = True
    fake._screen_routed = False
    fake._proactive_greeting_active = False
    fake._conversation_trace_id = "turn-1"
    return fake


def test_routing_away_from_the_stage_interrupts_the_active_turn():
    fake = _staged_window()

    TransparentWindow.set_stage_active(fake, False, True)

    fake._action_bus.cancel_conversation.assert_called_once()
    fake._motion_coordinator.interrupt_all.assert_called_once()
    fake._greeter.stop.assert_called_once()


def test_opening_a_modal_does_not_interrupt_the_active_turn():
    """modal 蓋在舞台上（角色還在後面）。自動彈出的素材 offer 不該把正在播的
    回覆攔腰切斷——那就是我們要修的「語音少一段」。"""
    fake = _staged_window()

    TransparentWindow.set_stage_active(fake, False, False)

    fake._action_bus.cancel_conversation.assert_not_called()
    fake._motion_coordinator.interrupt_all.assert_not_called()
    # 但打招呼仍要安靜下來
    fake._greeter.stop.assert_called_once()


def test_repeated_reports_while_on_a_screen_interrupt_only_once():
    """hit region 每次重報都會呼叫進來，不做邊緣偵測會在主選單裡反覆打 JS。"""
    fake = _staged_window()

    TransparentWindow.set_stage_active(fake, False, True)
    TransparentWindow.set_stage_active(fake, False, True)
    TransparentWindow.set_stage_active(fake, False, True)

    fake._motion_coordinator.interrupt_all.assert_called_once()


def test_entering_the_stage_does_not_interrupt():
    fake = MagicMock()
    fake._stage_active = False
    fake._screen_routed = True

    TransparentWindow.set_stage_active(fake, True, False)

    fake._action_bus.cancel_conversation.assert_not_called()
    fake._greeter.start.assert_called_once()


def test_switching_character_clears_and_interrupts():
    fake = MagicMock()
    fake._proactive_greeting_active = False
    fake._conversation_trace_id = "turn-1"

    TransparentWindow.on_character_switched(fake, {"character_id": "Choppr"})

    fake._motion_coordinator.interrupt_all.assert_called_once()
    fake.clear_conversation_turns.assert_called_once()
    fake.apply_character.assert_called_once_with("Choppr")


# ── 畫面重整不得偽造 XP 事件（#4）─────────────────────────────


def test_refresh_without_an_event_reports_zero_delta():
    fake = MagicMock()
    fake._adapter.get_current_state.return_value = {"xp": {"last_delta": 2, "progress_percent": 40}}

    TransparentWindow.refresh_agentic_ui(fake, message="Character switched.")

    args, _ = fake._run_javascript.call_args
    assert args[0] == "hydrateAgenticUI"
    assert args[1]["xp_delta"] == 0


def test_refresh_with_a_real_event_reports_the_real_delta():
    fake = MagicMock()
    fake._adapter.get_current_state.return_value = {"xp": {"last_delta": 2, "progress_percent": 40}}

    TransparentWindow.refresh_agentic_ui(fake, event_payload={"xp_delta": 2})

    args, _ = fake._run_javascript.call_args
    assert args[1]["xp_delta"] == 2


def test_refresh_without_an_event_ships_no_event_payload():
    """舊版會 fallback 到上一輪的 _latest_agentic_event，於是每次重整都把上一輪
    的回覆再送一次給前端。"""
    fake = MagicMock()
    fake._adapter.get_current_state.return_value = {"xp": {}}

    TransparentWindow.refresh_agentic_ui(fake, message="節慶事件已由 F 快捷鍵觸發。")

    args, _ = fake._run_javascript.call_args
    assert "event" not in args[1]


def test_refresh_with_an_event_ships_that_event():
    fake = MagicMock()
    fake._adapter.get_current_state.return_value = {"xp": {}}

    TransparentWindow.refresh_agentic_ui(fake, event_payload={"reply": "今天天氣不錯", "xp_delta": 2})

    args, _ = fake._run_javascript.call_args
    assert args[1]["event"]["reply"] == "今天天氣不錯"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
