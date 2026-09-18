"""interaction-pipeline-latency:時間線欄位、取消原因與冷/暖機統計聚合。

真實的 p50/p95 樣本要靠實機跑外部服務;這裡驗證的是「報表欄位齊全」與「統計沒算錯」,
不宣稱延遲已達標。
"""

from __future__ import annotations

import logging

import pytest

from pet_harness.character import generation as character_generation
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.latency import TurnTimeline, create_turn, summarize
from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)

CONTEXT = {"character_id": "char-Adol", "route_kind": "deterministic", "skill_name": None, "streaming": True, "slow_tool": False}


def test_report_exposes_every_required_stage_field():
    """3.1:缺的階段維持 null,不以 0 充當成功。"""
    report = TurnTimeline.create("turn-a", "vad", vad_endpoint=True).report(**CONTEXT)

    for field in (
        "route_source", "character_generation", "tool_status", "cancel_reason",
        "tts_request_ms", "tts_first_pcm_ms", "audio_start_ms", "endpoint_to_first_audio_ms",
    ):
        assert field in report, field
        assert report[field] is None, field
    assert report["budget_exceeded"] is None


def test_populated_stages_are_measured_end_to_end():
    timeline = TurnTimeline.create("turn-b", "vad", vad_endpoint=True)
    timeline.checkpoints.update({
        "vad_endpoint": 0.0, "stt_done": 0.4, "first_speech_chunk_emitted": 1.0,
        "tts_request_started": 1.05, "tts_first_pcm": 1.6, "audio_play_started": 1.75,
    })

    report = timeline.report(**CONTEXT, route_source="deterministic", character_generation=3, tool_status="success")

    assert report["tts_request_ms"] == 50
    assert report["tts_first_pcm_ms"] == 600
    assert report["audio_start_ms"] == 150
    assert report["endpoint_to_first_audio_ms"] == 1750
    assert (report["route_source"], report["character_generation"], report["tool_status"]) == ("deterministic", 3, "success")


def test_cancel_reason_survives_into_the_report_and_downgrades_the_warning():
    timeline = TurnTimeline.create("turn-c", "vad", vad_endpoint=True)
    timeline.cancel("stt_manual_stop")
    timeline.cancel("playback_interrupted")  # 只留第一個原因

    report = timeline.report(**CONTEXT)

    assert report["cancel_reason"] == "stt_manual_stop"
    assert report["timeline_complete"] is False  # 取消的回合本來就跑不完


def test_manual_stop_reports_an_unmeasured_endpoint_not_a_fake_low_latency():
    """3.4:手動停止收音沒有 VAD 端點,時間起點就是量不到,不得因此產生漂亮的數字,
    也不該被當成接線缺口。"""
    timeline = TurnTimeline.create("turn-manual", "vad", vad_endpoint=False)
    timeline.mark("stt_started")
    timeline.mark("stt_done")
    timeline.mark("route_done")
    timeline.mark("audio_play_started")
    timeline.mark("turn_complete")

    report = timeline.report(**CONTEXT, route_source="deterministic")

    assert report["endpoint_to_first_audio_ms"] is None
    assert report["budget_exceeded"] is None
    assert "vad_endpoint" not in report["missing_checkpoints"]


def test_character_generation_advances_so_late_results_are_recognisable():
    before = character_generation.current()
    assert character_generation.advance() == before + 1
    assert character_generation.current() == before + 1


# --------------------------------------------------------------------------
# 3.2 冷/暖機統計
# --------------------------------------------------------------------------

def _row(character_id, route, warm, first_audio, tool=None, cancel=None):
    return {
        "character_id": character_id, "route_kind": "conversation", "skill_name": route,
        "warmup_complete_before_turn": warm, "endpoint_to_first_audio_ms": first_audio,
        "tool_ms": tool, "cancel_reason": cancel,
    }


def test_summarize_splits_cold_and_warm_per_character_and_route():
    rows = [
        _row("char-Adol", "chat", True, 1000), _row("char-Adol", "chat", True, 2000),
        _row("char-Adol", "chat", False, 9000),
        _row("char-Jack", "chat", True, 1500),
    ]

    summary = {(item["character_id"], item["warmup"]): item for item in summarize(rows, budget_ms=3000)}

    assert summary[("char-Adol", "warm")]["samples"] == 2
    assert summary[("char-Adol", "warm")]["first_audio_p50_ms"] == 1000
    assert summary[("char-Adol", "warm")]["first_audio_p95_ms"] == 2000
    assert summary[("char-Adol", "warm")]["over_budget_rate"] == 0.0
    assert summary[("char-Adol", "cold")]["over_budget_rate"] == 1.0
    assert summary[("char-Jack", "warm")]["samples"] == 1


def test_unmeasurable_and_cancelled_turns_count_as_failures_not_as_good_numbers():
    rows = [
        _row("char-Kai", "chat", True, 1000),
        _row("char-Kai", "chat", True, None),                 # 沒量到首段音訊
        _row("char-Kai", "chat", True, 1200, cancel="stream_interrupted"),
    ]

    summary = summarize(rows, budget_ms=3000)[0]

    assert summary["samples"] == 3
    assert summary["measured_samples"] == 2
    assert summary["failure_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert summary["first_audio_p50_ms"] in (1000, 1200)


def test_summarize_returns_nothing_for_no_samples():
    assert summarize([]) == []


def test_slow_tool_turn_still_logs_latency_when_audio_finished_before_context_is_set(harness_env, caplog):
    """fix-media-action-motion-dispatch: post-LLM 的 slow tool（如 play_music）
    可能讓 turn_complete 遠遠晚於語音已經起播（甚至播畢）；audio_worker 稍早呼叫
    log_current() 時 context 還是空的，靜默略過。handle_event() 必須在
    set_context() 之後自行補記，否則這類回合完全沒有 [TURN LATENCY] 紀錄。"""
    tmp_path, agentic_root = harness_env
    engine = PetHarnessEngine(
        FakeProvider(), agentic_root=agentic_root, db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json", character_id="Choppr",
    )
    turn_id = "turn-slow-tool-early-audio"
    timeline = create_turn(turn_id, "text")
    timeline.mark("audio_play_started")  # 模擬語音已經在 turn_complete 之前就起播（甚至播畢）

    with caplog.at_level(logging.INFO, logger="pet_harness.latency"):
        engine.handle_event({"text": "totally unmatched text xyz", "metadata": {"turn_id": turn_id}})

    records = [r for r in caplog.records if r.getMessage().startswith("[TURN LATENCY]")]
    assert len(records) == 1
    assert f"'turn_id': '{turn_id}'" in records[0].getMessage()


def test_normal_turn_without_early_audio_is_not_double_logged(harness_env, caplog):
    """對照組：audio_play_started 尚未起播（一般情形）時，handle_event() 不搶著
    補記，避免和 audio_worker 稍後的 log_current() 重複記錄同一輪。"""
    tmp_path, agentic_root = harness_env
    engine = PetHarnessEngine(
        FakeProvider(), agentic_root=agentic_root, db_path=tmp_path / "state.db",
        snapshot_path=tmp_path / "debug" / "latest_pet_event.json", character_id="Choppr",
    )
    turn_id = "turn-normal-no-early-audio"
    create_turn(turn_id, "text")  # audio_play_started 未標記

    with caplog.at_level(logging.INFO, logger="pet_harness.latency"):
        engine.handle_event({"text": "totally unmatched text xyz", "metadata": {"turn_id": turn_id}})

    records = [r for r in caplog.records if r.getMessage().startswith("[TURN LATENCY]")]
    assert len(records) == 0
