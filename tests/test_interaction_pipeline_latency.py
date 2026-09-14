"""interaction-pipeline-latency:時間線欄位、取消原因與冷/暖機統計聚合。

真實的 p50/p95 樣本要靠實機跑外部服務;這裡驗證的是「報表欄位齊全」與「統計沒算錯」,
不宣稱延遲已達標。
"""

from __future__ import annotations

import pytest

from pet_harness.character import generation as character_generation
from pet_harness.latency import TurnTimeline, summarize

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
