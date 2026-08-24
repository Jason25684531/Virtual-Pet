from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from pet_harness.latency import TurnTimeline, claim_voice_turn, create_voice_turn, get_turn
from pet_harness.memory.base_memory_store import MemoryStoreStatus
from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever


def test_timeline_keeps_optional_checkpoints_null_and_classifies_adjacent_stage():
    timeline = TurnTimeline.create("turn-1", "text")
    timeline.checkpoints.update({
        "llm_request_started": 1.0,
        "llm_first_token": 3.5,
        "first_speech_chunk_emitted": 3.7,
    })

    report = timeline.report(character_id="miku", route_kind="conversation", skill_name=None, streaming=True, slow_tool=False)

    assert report["endpoint_to_first_audio_ms"] is None
    assert report["tts_first_pcm_ms"] is None
    assert report["llm_ttft_ms"] == 2500
    assert report["bottleneck_stage"] == "llm_ttft"


def test_budget_exceeded_is_null_not_false_when_first_audio_unmeasurable():
    """A metric that couldn't be computed is unmeasurable, not "within budget" —
    collapsing missing data into False would silently report a fake pass."""
    timeline = TurnTimeline.create("turn-2", "vad", vad_endpoint=True)

    report = timeline.report(character_id="miku", route_kind="conversation", skill_name=None, streaming=True, slow_tool=False)

    assert report["endpoint_to_first_audio_ms"] is None
    assert report["budget_exceeded"] is None


def test_timeline_complete_flags_missing_checkpoints_on_voice_streaming_turn():
    timeline = TurnTimeline.create("turn-3", "vad", vad_endpoint=True)
    timeline.mark("stt_started")
    timeline.mark("stt_done")
    timeline.mark("route_done")
    timeline.mark("turn_complete")
    # llm_*/tts_*/audio_play_started deliberately left unset.

    report = timeline.report(character_id="miku", route_kind="conversation", skill_name=None, streaming=True, slow_tool=False)

    assert report["timeline_complete"] is False
    assert "llm_first_token" in report["missing_checkpoints"]
    assert "audio_play_started" in report["missing_checkpoints"]
    assert "vad_endpoint" not in report["missing_checkpoints"]


def test_timeline_complete_true_when_ack_only_turn_has_no_llm_or_pre_llm_checkpoints():
    """ack_only turns skip the LLM entirely; they must not be flagged incomplete
    for checkpoints that never apply to that turn shape."""
    timeline = TurnTimeline.create("turn-4", "vad", vad_endpoint=True)
    for checkpoint in ("stt_started", "stt_done", "route_done", "tool_started", "tool_done",
                       "first_speech_chunk_emitted", "tts_request_started", "tts_first_pcm",
                       "audio_play_started", "turn_complete"):
        timeline.mark(checkpoint)

    report = timeline.report(character_id="miku", route_kind="deterministic", skill_name="youtube_music_playback", streaming=True, slow_tool=True)

    assert report["timeline_complete"] is True
    assert report["missing_checkpoints"] == []


def test_resolve_warmup_compares_against_turn_start_not_wall_clock_now():
    timeline = TurnTimeline.create("turn-5", "vad", vad_endpoint=True)
    turn_start = timeline.checkpoints["vad_endpoint"]

    timeline.resolve_warmup(turn_start - 1.0)
    assert timeline.warmup_complete_before_turn is True

    timeline.warmup_complete_before_turn = False  # reset for the next assertion
    timeline.resolve_warmup(turn_start + 1.0)
    assert timeline.warmup_complete_before_turn is False

    timeline.resolve_warmup(None)
    assert timeline.warmup_complete_before_turn is False


def test_mark_with_explicit_timestamp_is_first_write_wins():
    timeline = TurnTimeline.create("turn-6", "vad")

    timeline.mark("stt_started", at=5.0)
    timeline.mark("stt_started", at=10.0)

    assert timeline.checkpoints["stt_started"] == 5.0


def test_create_voice_turn_marks_raw_timestamps_and_resolves_warmup_without_pet_harness_import_in_sensors():
    """sensors/stt_controller.py must stay free of any pet_harness import (module-dependency-boundaries);
    it only emits raw perf_counter() floats, and this helper is what turns them into a timeline."""
    vad_ts, stt_started_ts, stt_done_ts = 100.0, 100.2, 100.6

    timeline = create_voice_turn(
        "voice-test-1",
        vad_endpoint_ts=vad_ts,
        stt_started_ts=stt_started_ts,
        stt_done_ts=stt_done_ts,
        warmup_completed_at=99.0,
    )

    assert timeline.checkpoints["vad_endpoint"] == vad_ts
    assert timeline.checkpoints["stt_started"] == stt_started_ts
    assert timeline.checkpoints["stt_done"] == stt_done_ts
    assert timeline.warmup_complete_before_turn is True
    assert get_turn("voice-test-1") is timeline
    assert claim_voice_turn() is timeline


def test_create_voice_turn_without_vad_endpoint_leaves_it_null_for_manual_stop():
    timeline = create_voice_turn(
        "voice-test-2", vad_endpoint_ts=None, stt_started_ts=1.0, stt_done_ts=1.4, warmup_completed_at=None,
    )

    assert timeline.checkpoints["vad_endpoint"] is None
    assert timeline.warmup_complete_before_turn is False
    claim_voice_turn()  # drain queue so it doesn't leak into other tests


class _Index:
    def __init__(self):
        self.queries = []

    def search(self, dense, sparse, top_k):
        self.queries.append((dense, sparse, top_k))
        return []


class _Sparse:
    def status(self):
        return MemoryStoreStatus("ready")

    def encode(self, text):
        assert text == "記憶預熱"
        return {1: 1.0}


def test_retriever_warmup_uses_non_empty_read_path_without_writes():
    index = _Index()
    retriever = ContextualMemoryRetriever(index, lambda text: [float(len(text))], _Sparse())

    result = retriever.warmup("miku")

    assert result.evidence == []
    assert index.queries == [([4.0], {1: 1.0}, 5)]


def test_warmup_and_retrieval_can_share_the_read_only_retriever():
    index = _Index()
    retriever = ContextualMemoryRetriever(index, lambda _text: [1.0], _Sparse())

    with ThreadPoolExecutor(max_workers=2) as executor:
        warmup, retrieval = executor.submit(retriever.warmup, "miku"), executor.submit(retriever.warmup, "miku")
        assert warmup.result().evidence == retrieval.result().evidence == []

    assert len(index.queries) == 2
