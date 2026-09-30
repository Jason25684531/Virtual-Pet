"""Small, dependency-free turn latency timeline shared across the voice pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter
from typing import Any

LOGGER = logging.getLogger(__name__)
CHECKPOINTS = (
    "vad_endpoint", "stt_started", "stt_done", "route_done", "retrieval_done",
    "pre_llm_done", "tool_started", "tool_done", "llm_request_started",
    "llm_first_token", "first_speech_chunk_emitted", "llm_done",
    "tts_request_started", "tts_first_pcm", "audio_play_started", "turn_complete",
)
_TIMELINES: dict[str, TurnTimeline] = {}
_TIMELINES_LOCK = RLock()
_PENDING_VOICE_TURNS: list[str] = []


@dataclass
class TurnTimeline:
    turn_id: str
    origin: str
    checkpoints: dict[str, float | None] = field(default_factory=lambda: dict.fromkeys(CHECKPOINTS))
    warmup_complete_before_turn: bool = False
    ack_emitted: bool = False
    cancel_reason: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock, repr=False)

    @classmethod
    def create(cls, turn_id: str, origin: str, *, vad_endpoint: bool = False, warmup_complete: bool = False) -> TurnTimeline:
        timeline = cls(turn_id=turn_id, origin=origin, warmup_complete_before_turn=warmup_complete)
        if vad_endpoint:
            timeline.mark("vad_endpoint")
        return timeline

    def mark(self, checkpoint: str, at: float | None = None) -> None:
        if checkpoint not in self.checkpoints:
            raise ValueError(f"unknown latency checkpoint: {checkpoint}")
        with self._lock:
            if self.checkpoints[checkpoint] is None:
                self.checkpoints[checkpoint] = at if at is not None else perf_counter()

    def cancel(self, reason: str) -> None:
        """記下這條時間線為何提早結束;只留第一個原因(最接近真正的觸發點)。"""
        with self._lock:
            if self.cancel_reason is None:
                self.cancel_reason = reason

    def resolve_warmup(self, warmup_completed_at: float | None) -> None:
        """Compare against this turn's own start time, not wall-clock "now", so a
        warmup finishing during STT/engine processing is still correctly reported
        as not-yet-complete at the moment the turn actually began."""
        turn_start = self.checkpoints.get("vad_endpoint") or perf_counter()
        self.warmup_complete_before_turn = warmup_completed_at is not None and warmup_completed_at <= turn_start

    def ms(self, start: str, end: str) -> int | None:
        with self._lock:
            left, right = self.checkpoints.get(start), self.checkpoints.get(end)
        return None if left is None or right is None else max(0, round((right - left) * 1000))

    def classify(self) -> str | None:
        stages = {
            "stt": self.ms("vad_endpoint", "stt_done"),
            "retrieval": self.ms("stt_done", "llm_request_started"),
            "tool": self.ms("tool_started", "tool_done"),
            "llm_ttft": self.ms("llm_request_started", "llm_first_token"),
            "llm_chunking": self.ms("llm_first_token", "first_speech_chunk_emitted"),
            "tts": self.ms("first_speech_chunk_emitted", "tts_first_pcm"),
            "audio_start": self.ms("tts_first_pcm", "audio_play_started"),
        }
        present = {name: value for name, value in stages.items() if value is not None}
        return max(present, key=present.get) if present else None

    def _expected_checkpoints(self, *, streaming: bool, slow_tool: bool) -> tuple[str, ...]:
        """Checkpoints this turn's own shape should produce; used to flag
        wiring gaps without demanding checkpoints that never apply (e.g. audio
        checkpoints on a text turn, LLM checkpoints on an ack-only turn)."""
        expected: list[str] = ["route_done", "turn_complete"]
        if self.origin == "vad":
            # vad_endpoint is NOT required even on a voice turn: manual/device/max
            # stop reasons never trigger it (see sensors/stt_controller.py), and that
            # is expected behavior, not a wiring gap.
            expected += ["stt_started", "stt_done"]
        if slow_tool:
            expected += ["tool_started", "tool_done"]
        else:
            expected += ["pre_llm_done", "llm_request_started", "llm_first_token", "llm_done"]
        if streaming:
            expected.append("first_speech_chunk_emitted")
        if self.origin == "vad" and streaming:
            expected += ["tts_request_started", "tts_first_pcm", "audio_play_started"]
        return tuple(expected)

    def report(
        self,
        *,
        character_id: str | None,
        route_kind: str,
        skill_name: str | None,
        streaming: bool,
        slow_tool: bool,
        route_source: str | None = None,
        character_generation: int | None = None,
        tool_status: str | None = None,
        cancel_reason: str | None = None,
    ) -> dict[str, Any]:
        import config

        endpoint_to_audio = self.ms("vad_endpoint", "audio_play_started")
        missing = [name for name in self._expected_checkpoints(streaming=streaming, slow_tool=slow_tool) if self.checkpoints.get(name) is None]
        data = {
            "turn_id": self.turn_id, "character_id": character_id, "route_kind": route_kind,
            "skill_name": skill_name, "streaming": streaming, "slow_tool": slow_tool,
            # route_source 區分 deterministic/semantic/provider;character_generation 讓切角後
            # 遲到的回合可以被認出來;tool_status 區分「確認語音已說」與「工具真的完成」;
            # cancel_reason 說明這條時間線為何提早結束。不適用的階段一律留 null,不以 0 充數。
            "route_source": route_source, "character_generation": character_generation,
            "tool_status": tool_status, "cancel_reason": cancel_reason or self.cancel_reason,
            "endpoint_to_stt_ms": self.ms("vad_endpoint", "stt_done"),
            "retrieval_ms": self.ms("stt_done", "pre_llm_done"), "tool_ms": self.ms("tool_started", "tool_done"),
            "llm_ttft_ms": self.ms("llm_request_started", "llm_first_token"),
            "first_speech_chunk_ms": self.ms("vad_endpoint", "first_speech_chunk_emitted"),
            "tts_request_ms": self.ms("first_speech_chunk_emitted", "tts_request_started"),
            "tts_first_pcm_ms": self.ms("first_speech_chunk_emitted", "tts_first_pcm"),
            "audio_start_ms": self.ms("tts_first_pcm", "audio_play_started"),
            "endpoint_to_first_audio_ms": endpoint_to_audio,
            "turn_complete_ms": self.ms("vad_endpoint", "turn_complete"),
            "warmup_complete_before_turn": self.warmup_complete_before_turn,
            "measurement_semantics": "pcm_submitted", "bottleneck_stage": self.classify(),
            "timeline_complete": not missing, "missing_checkpoints": missing,
        }
        # A metric that couldn't be computed is unmeasurable, not "within budget" —
        # collapsing that into False would silently report a fake pass.
        data["budget_exceeded"] = None if endpoint_to_audio is None else endpoint_to_audio > config.TURN_LATENCY_BUDGET_MS
        return data

    def log(self, **kwargs: Any) -> dict[str, Any]:
        data = self.report(**kwargs)
        # 被取消的回合本來就跑不完後面的階段,那是預期行為而不是接線缺口。
        incomplete = (data["budget_exceeded"] or not data["timeline_complete"]) and not data["cancel_reason"]
        log = LOGGER.warning if incomplete else LOGGER.info
        log("[TURN LATENCY] %s", data)
        return data

    def set_context(self, **kwargs: Any) -> None:
        self.context = kwargs

    def log_current(self) -> dict[str, Any] | None:
        # ponytail: for a streaming turn, audio_play_started can fire while the LLM is
        # still generating — i.e. before handle_event() reaches set_context() at turn end.
        # report() needs character_id/route_kind/skill_name/streaming/slow_tool, none of
        # which are knowable yet at that point (matched_skill isn't resolved until after
        # the LLM reply is parsed). Skip silently; the authoritative [TURN LATENCY] entry
        # still fires once handle_event() finishes and calls set_context().
        if not self.context:
            return None
        return self.log(**self.context)


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(fraction * (len(ordered) - 1))]


def summarize(reports, *, budget_ms: int | None = None) -> list[dict[str, Any]]:
    """把多筆 report() 聚成每角色 × 每路由 × 冷/暖機的 p50、p95 與失敗率。

    樣本要靠真實服務跑出來(3.2 的量測步驟);這裡只負責聚合,讓離線測試也能驗證
    統計本身沒算錯。沒有量到 endpoint_to_first_audio_ms 的回合算進 samples 與
    failures,但不進百分位數 —— 量不到就是量不到,不能拿來充當一個好成績。
    """
    import config

    budget = config.TURN_LATENCY_BUDGET_MS if budget_ms is None else budget_ms
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for report in reports:
        key = (
            report.get("character_id"),
            report.get("skill_name") or report.get("route_kind") or "conversation",
            "warm" if report.get("warmup_complete_before_turn") else "cold",
        )
        groups.setdefault(key, []).append(report)

    summary = []
    for (character_id, route, warmup), rows in sorted(groups.items(), key=lambda item: [str(part) for part in item[0]]):
        first_audio = [row["endpoint_to_first_audio_ms"] for row in rows if row.get("endpoint_to_first_audio_ms") is not None]
        tool = [row["tool_ms"] for row in rows if row.get("tool_ms") is not None]
        failures = [row for row in rows if row.get("cancel_reason") or row.get("endpoint_to_first_audio_ms") is None]
        summary.append({
            "character_id": character_id, "route": route, "warmup": warmup,
            "samples": len(rows), "measured_samples": len(first_audio),
            "first_audio_p50_ms": _percentile(first_audio, 0.50),
            "first_audio_p95_ms": _percentile(first_audio, 0.95),
            "tool_p50_ms": _percentile(tool, 0.50), "tool_p95_ms": _percentile(tool, 0.95),
            "failure_rate": round(len(failures) / len(rows), 4),
            "over_budget_rate": round(sum(value > budget for value in first_audio) / len(first_audio), 4) if first_audio else None,
            "budget_ms": budget,
        })
    return summary


def create_turn(turn_id: str, origin: str, *, vad_endpoint: bool = False, warmup_complete: bool = False) -> TurnTimeline:
    timeline = TurnTimeline.create(turn_id, origin, vad_endpoint=vad_endpoint, warmup_complete=warmup_complete)
    with _TIMELINES_LOCK:
        _TIMELINES[turn_id] = timeline
    return timeline


def create_voice_turn(
    turn_id: str,
    *,
    vad_endpoint_ts: float | None,
    stt_started_ts: float,
    stt_done_ts: float,
    warmup_completed_at: float | None,
) -> TurnTimeline:
    """Build the voice-origin timeline from raw perf_counter() floats captured
    in sensors/stt_controller.py. Kept here (not in sensors/) so sensors/ stays
    free of any pet_harness import per module-dependency-boundaries."""
    timeline = create_turn(turn_id, "vad")
    if vad_endpoint_ts is not None:
        timeline.mark("vad_endpoint", at=vad_endpoint_ts)
    timeline.mark("stt_started", at=stt_started_ts)
    timeline.mark("stt_done", at=stt_done_ts)
    timeline.resolve_warmup(warmup_completed_at)
    queue_voice_turn(timeline.turn_id)
    return timeline


def get_turn(turn_id: str | None) -> TurnTimeline | None:
    with _TIMELINES_LOCK:
        return _TIMELINES.get(str(turn_id or ""))


def rekey_turn(timeline: TurnTimeline, turn_id: str) -> TurnTimeline:
    """Bind the VAD-originated timeline to the UI trace used by playback."""
    with _TIMELINES_LOCK:
        _TIMELINES.pop(timeline.turn_id, None)
        timeline.turn_id = turn_id
        _TIMELINES[turn_id] = timeline
    return timeline


def queue_voice_turn(turn_id: str) -> None:
    with _TIMELINES_LOCK:
        _PENDING_VOICE_TURNS.append(turn_id)


def claim_voice_turn() -> TurnTimeline | None:
    with _TIMELINES_LOCK:
        return _TIMELINES.get(_PENDING_VOICE_TURNS.pop(0)) if _PENDING_VOICE_TURNS else None
