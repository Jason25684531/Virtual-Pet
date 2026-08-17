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
    timeout_promoted: bool = False
    fallback_triggered: bool = False
    selected_tts_provider: str = ""
    text_only_completed: bool = False
    tts_skipped_by_design: bool = False
    finalized: bool = False


class InteractionLatencyTracker:
    """記錄本地快捷動作從觸發到 TTS 播放完成的耗時切面。"""

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

    def mark_action_dispatched(self, trace_id: str | None, action_name: str):
        self._record(trace_id, "first_action_dispatched", f"motion 命中 `{action_name}`", first_only=True)

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
                state.notes["first_tts_enqueued"] = f"TTS 已排入佇列：{_preview_text(text)}"
                elapsed_ms = self._elapsed_ms(state, "first_tts_enqueued")
            else:
                elapsed_ms = self._elapsed_ms(state, "first_tts_enqueued")
        if elapsed_ms is not None:
            if chunk_index == 1:
                self._log(trace_id, f"TTS 已排入佇列 (+{elapsed_ms}ms)")
            else:
                self._log(trace_id, f"第{chunk_index}段 TTS 已排入佇列 (+{elapsed_ms}ms)")

    def mark_tts_stream_started(self, trace_id: str | None, reply_id: str, bytes_forwarded: int):
        detail = f"TTS 開始送入播放器，reply={reply_id[:8]}，bytes={bytes_forwarded}"
        self._record(trace_id, "first_tts_stream_started", detail, first_only=True)

    def mark_driver_started(self, trace_id: str | None, reply_id: str):
        detail = f"播放驅動開始接手音訊，reply={reply_id[:8]}"
        self._record(trace_id, "first_driver_started", detail, first_only=True)

    def mark_timeout_promoted(self, trace_id: str | None, action_name: str):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.timeout_promoted = True
        self._record(
            trace_id,
            "timeout_promoted",
            f"Pending action 超時升級為正式動作：{action_name}",
            first_only=True,
        )

    def mark_tts_provider_selected(self, trace_id: str | None, provider: str, reason: str = ""):
        normalized_provider = str(provider or "").strip()
        if not trace_id or not normalized_provider:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.selected_tts_provider = normalized_provider
        detail = f"TTS provider 已選定：{normalized_provider}"
        if reason:
            detail = f"{detail} ({reason})"
        self._record(trace_id, "tts_provider_selected", detail, first_only=False)

    def mark_tts_fallback_triggered(
        self,
        trace_id: str | None,
        from_provider: str,
        to_provider: str,
        reason_code: str = "",
    ):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.fallback_triggered = True
            state.selected_tts_provider = str(to_provider or "").strip() or state.selected_tts_provider
        detail = f"voai_failed_triggering_fallback：{from_provider}->{to_provider}"
        if reason_code:
            detail = f"{detail} ({reason_code})"
        self._record(trace_id, "voai_failed_triggering_fallback", detail, first_only=True)

    def mark_text_only_completed(self, trace_id: str | None, provider_chain: str = ""):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.text_only_completed = True
        detail = "TTS 全部失敗，改為文字-only 完成"
        if provider_chain:
            detail = f"{detail} ({provider_chain})"
        self._record(trace_id, "text_only_completed", detail, first_only=True)

    def mark_tts_skipped_by_design(self, trace_id: str | None, reason: str = ""):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.tts_skipped_by_design = True
        detail = reason.strip() or "TTS 依設計略過"
        self._record(trace_id, "tts_skipped_by_design", detail, first_only=True)

    def mark_tts_finished(
        self,
        trace_id: str | None,
        reply_id: str,
        success: bool,
        message: str,
        skipped_by_design: bool = False,
    ):
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
            if skipped_by_design:
                state.tts_skipped_by_design = True
                note = f"reply={reply_id[:8]}，依設計略過：{message}"
            else:
                note = f"reply={reply_id[:8]}，{'成功' if success else '失敗'}：{message}"
            state.notes["last_tts_finished"] = note
            if not success and not skipped_by_design:
                state.tts_failures += 1
            elapsed_ms = self._elapsed_from(state, now)
            should_finalize = self._should_finalize(state)
        if elapsed_ms is not None:
            self._log(trace_id, f"TTS 已完成 (+{elapsed_ms}ms) {note}")
        if should_finalize:
            self._finalize(trace_id)

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
        if state.finalized:
            return False
        return state.tts_enqueued > 0 and state.tts_finished >= state.tts_enqueued

    def _finalize(self, trace_id: str):
        with self._lock:
            state = self._traces.pop(trace_id, None)
            if state is None or state.finalized:
                return
            state.finalized = True
            end_time = perf_counter()
            state.stages["interaction_completed"] = end_time
            state.notes["interaction_completed"] = "整段互動已完成"
            summary = self._build_summary_text(self._build_summary_payload(state))
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

    def _build_summary_payload(self, state: InteractionTraceState) -> dict[str, object]:
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

        add_delta("tts_startup", "first_tts_enqueued", "first_tts_stream_started")
        add_delta("tts_to_driver_start", "first_tts_stream_started", "first_driver_started")
        add_delta("dispatch_to_first_audio", "first_action_dispatched", "first_driver_started")
        add_delta("tts_tail", "first_tts_stream_started", "interaction_completed")
        add_delta("dispatch_to_complete", "first_action_dispatched", "interaction_completed")

        bottleneck_label = "n/a"
        bottleneck_ms = 0
        if stage_durations:
            bottleneck_label, bottleneck_ms = max(stage_durations, key=lambda item: item[1])

        total_ms = self._elapsed_ms(state, "interaction_completed") or 0
        milestones = []
        for stage in (
            "first_action_dispatched",
            "first_tts_stream_started",
            "first_driver_started",
            "timeout_promoted",
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
        provider_label = state.selected_tts_provider or "unknown"

        return {
            "trace_id": state.trace_id,
            "source": state.source,
            "input_text": state.input_text,
            "total_ms": total_ms,
            "stage_durations": dict(stage_durations),
            "bottleneck_label": bottleneck_label,
            "bottleneck_ms": bottleneck_ms,
            "milestones": milestones,
            "tts_failures": state.tts_failures,
            "timeout_promoted": state.timeout_promoted,
            "fallback_triggered": state.fallback_triggered,
            "selected_tts_provider": provider_label,
            "text_only_completed": state.text_only_completed,
            "tts_skipped_by_design": state.tts_skipped_by_design,
            "failure_suffix": failure_suffix,
            "stage_parts": stage_parts,
        }

    @staticmethod
    def _build_summary_text(summary_payload: dict[str, object]) -> str:
        stage_parts = list(summary_payload.get("stage_parts", []))
        milestones = list(summary_payload.get("milestones", []))
        failure_suffix = str(summary_payload.get("failure_suffix", ""))
        provider_suffix = (
            f" | provider={summary_payload.get('selected_tts_provider', 'unknown')}"
            f" fallback_triggered={summary_payload.get('fallback_triggered', False)}"
            f" text_only={summary_payload.get('text_only_completed', False)}"
            f" tts_skipped_by_design={summary_payload.get('tts_skipped_by_design', False)}"
        )
        return (
            "互動完成摘要 "
            f"source={summary_payload.get('source', 'unknown')} "
            f"total={summary_payload.get('total_ms', 0)}ms | "
            f"stages: {'; '.join(stage_parts)} | "
            f"bottleneck={summary_payload.get('bottleneck_label', 'n/a')}({summary_payload.get('bottleneck_ms', 0)}ms) | "
            f"milestones: {'; '.join(milestones)}{failure_suffix}{provider_suffix}"
        )

    @staticmethod
    def _log(trace_id: str, message: str):
        print(f"[ECHOES][TRACE][{trace_id}] {message}")
