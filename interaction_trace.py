from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from uuid import uuid4


def _preview_text(text: str, limit: int = 32) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _ms(value: float | None) -> int | None:
    if value is None:
        return None
    return max(0, round(value * 1000))


@dataclass
class InteractionTraceState:
    trace_id: str
    source: str
    input_text: str
    started_at: float
    stages: dict[str, float] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    tts_enqueued: int = 0
    tts_finished: int = 0
    tts_failures: int = 0
    brain_completed: bool = False
    finalized: bool = False


class InteractionLatencyTracker:
    """記錄從收到文字到整段互動結束的耗時切面。"""

    def __init__(self):
        self._lock = Lock()
        self._traces: dict[str, InteractionTraceState] = {}

    def begin_interaction(self, source: str, text: str) -> str:
        trace_id = uuid4().hex[:8]
        started_at = perf_counter()
        state = InteractionTraceState(
            trace_id=trace_id,
            source=str(source or "unknown").strip() or "unknown",
            input_text=str(text or "").strip(),
            started_at=started_at,
        )
        state.stages["text_received"] = started_at
        with self._lock:
            self._traces[trace_id] = state
        self._log(trace_id, f"收到文字，source={state.source}，text={_preview_text(state.input_text)}")
        return trace_id

    def abort(self, trace_id: str | None, reason: str):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.pop(trace_id, None)
        if state is None:
            return
        self._log(trace_id, f"追蹤已中止：{reason}")

    def mark_brain_queued(self, trace_id: str | None):
        self._record(trace_id, "brain_queued", "已送入 BrainEngine 佇列")

    def mark_brain_started(self, trace_id: str | None):
        self._record(trace_id, "brain_started", "BrainEngine 開始處理")

    def mark_fragment_emitted(self, trace_id: str | None, fragment: str):
        if not trace_id:
            return
        normalized = str(fragment or "").strip()
        if not normalized:
            return
        self._record(trace_id, "first_brain_output", "收到第一個大腦輸出片段", first_only=True)
        if normalized.startswith("[ACTION:"):
            self._record(trace_id, "first_action_fragment", f"第一個 action 片段：{normalized}", first_only=True)
            return
        self._record(
            trace_id,
            "first_text_fragment",
            f"第一個文字片段：{_preview_text(normalized)}",
            first_only=True,
        )

    def mark_action_dispatched(self, trace_id: str | None, action_name: str):
        self._record(trace_id, "first_action_dispatched", f"ActionDispatcher 命中 `{action_name}`", first_only=True)

    def mark_tts_enqueued(self, trace_id: str | None, reply_id: str, text: str):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.tts_enqueued += 1
            chunk_index = state.tts_enqueued
            if "first_tts_enqueued" not in state.stages:
                state.stages["first_tts_enqueued"] = perf_counter()
                state.notes["first_tts_enqueued"] = f"第一段 TTS 已排入佇列：{_preview_text(text)}"
                elapsed_ms = self._elapsed_ms(state, "first_tts_enqueued")
            else:
                elapsed_ms = self._elapsed_ms(state, "first_tts_enqueued")
        if elapsed_ms is not None:
            ordinal = "第一" if chunk_index == 1 else f"第{chunk_index}"
            self._log(trace_id, f"{ordinal}段 TTS 已排入佇列 (+{elapsed_ms}ms)")

    def mark_tts_stream_started(self, trace_id: str | None, reply_id: str, bytes_forwarded: int):
        detail = f"TTS 開始送入播放器，reply={reply_id[:8]}，bytes={bytes_forwarded}"
        self._record(trace_id, "first_tts_stream_started", detail, first_only=True)

    def mark_tts_finished(self, trace_id: str | None, reply_id: str, success: bool, message: str):
        if not trace_id:
            return
        should_finalize = False
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            now = perf_counter()
            state.tts_finished += 1
            state.stages["last_tts_finished"] = now
            note = f"reply={reply_id[:8]}，{'成功' if success else '失敗'}：{message}"
            state.notes["last_tts_finished"] = note
            if not success:
                state.tts_failures += 1
            elapsed_ms = self._elapsed_from(state, now)
            should_finalize = self._should_finalize(state)
        if elapsed_ms is not None:
            self._log(trace_id, f"TTS 片段已完成 (+{elapsed_ms}ms) {note}")
        if should_finalize:
            self._finalize(trace_id)

    def mark_brain_completed(self, trace_id: str | None):
        if not trace_id:
            return
        should_finalize = False
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.brain_completed = True
            now = perf_counter()
            state.stages["brain_completed"] = now
            state.notes["brain_completed"] = "BrainEngine 串流完成"
            elapsed_ms = self._elapsed_from(state, now)
            should_finalize = self._should_finalize(state)
        if elapsed_ms is not None:
            self._log(trace_id, f"BrainEngine 已完成 (+{elapsed_ms}ms)")
        if should_finalize:
            self._finalize(trace_id)

    def mark_failure(self, trace_id: str | None, stage: str, message: str):
        stage_name = f"{stage}_failed"
        self._record(trace_id, stage_name, message, first_only=True)

    def snapshot(self, trace_id: str) -> dict[str, object] | None:
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return None
            return {
                "trace_id": state.trace_id,
                "source": state.source,
                "input_text": state.input_text,
                "stages": dict(state.stages),
                "notes": dict(state.notes),
                "tts_enqueued": state.tts_enqueued,
                "tts_finished": state.tts_finished,
                "brain_completed": state.brain_completed,
                "finalized": state.finalized,
            }

    def _record(self, trace_id: str | None, stage: str, detail: str, first_only: bool = True):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            if first_only and stage in state.stages:
                return
            now = perf_counter()
            state.stages[stage] = now
            state.notes[stage] = detail
            elapsed_ms = self._elapsed_from(state, now)
        if elapsed_ms is not None:
            self._log(trace_id, f"{detail} (+{elapsed_ms}ms)")

    def _should_finalize(self, state: InteractionTraceState) -> bool:
        if state.finalized or not state.brain_completed:
            return False
        return state.tts_finished >= state.tts_enqueued

    def _finalize(self, trace_id: str):
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None or state.finalized:
                return
            state.finalized = True
            end_time = perf_counter()
            state.stages["interaction_completed"] = end_time
            state.notes["interaction_completed"] = "整段互動已完成"
            summary = self._build_summary(state)
            self._traces.pop(trace_id, None)
        self._log(trace_id, summary)

    @staticmethod
    def _elapsed_from(state: InteractionTraceState, timestamp: float) -> int | None:
        return _ms(timestamp - state.started_at)

    @staticmethod
    def _elapsed_ms(state: InteractionTraceState, stage: str) -> int | None:
        timestamp = state.stages.get(stage)
        if timestamp is None:
            return None
        return _ms(timestamp - state.started_at)

    def _build_summary(self, state: InteractionTraceState) -> str:
        stage_durations: list[tuple[str, int]] = []

        def add_delta(label: str, start_stage: str, end_stage: str):
            start = state.stages.get(start_stage)
            end = state.stages.get(end_stage)
            if start is None or end is None or end < start:
                return
            duration = _ms(end - start)
            if duration is None:
                return
            stage_durations.append((label, duration))

        first_output_stage = None
        for candidate in ("first_action_fragment", "first_text_fragment", "first_brain_output"):
            if candidate in state.stages:
                first_output_stage = candidate
                break

        add_delta("brain_queue_wait", "brain_queued", "brain_started")
        if first_output_stage is not None:
            add_delta("llm_to_first_output", "brain_started", first_output_stage)
        add_delta("tts_startup", "first_tts_enqueued", "first_tts_stream_started")
        add_delta("tts_tail", "first_tts_stream_started", "interaction_completed")
        add_delta("post_brain_tail", "brain_completed", "interaction_completed")

        bottleneck_label = "n/a"
        bottleneck_ms = 0
        if stage_durations:
            bottleneck_label, bottleneck_ms = max(stage_durations, key=lambda item: item[1])

        total_ms = self._elapsed_ms(state, "interaction_completed") or 0
        milestones = []
        for stage in (
            "first_action_fragment",
            "first_text_fragment",
            "first_action_dispatched",
            "first_tts_stream_started",
            "brain_completed",
        ):
            stage_ms = self._elapsed_ms(state, stage)
            if stage_ms is None:
                continue
            milestones.append(f"{stage}={stage_ms}ms")

        stage_parts = [f"{label}={duration}ms" for label, duration in stage_durations]
        if not stage_parts:
            stage_parts.append("no-stage-deltas")
        if not milestones:
            milestones.append("no-milestones")

        failure_suffix = ""
        if state.tts_failures:
            failure_suffix = f" | tts_failures={state.tts_failures}"

        return (
            "互動完成摘要 "
            f"source={state.source} total={total_ms}ms | "
            f"stages: {'; '.join(stage_parts)} | "
            f"bottleneck={bottleneck_label}({bottleneck_ms}ms) | "
            f"milestones: {'; '.join(milestones)}{failure_suffix}"
        )

    @staticmethod
    def _log(trace_id: str, message: str):
        print(f"[ECHOES][TRACE][{trace_id}] {message}")
