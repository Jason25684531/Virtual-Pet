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
    tts_expected: int = 0
    tts_enqueued: int = 0
    tts_finished: int = 0
    tts_failures: int = 0
    timeout_promoted: bool = False
    fallback_triggered: bool = False
    selected_tts_provider: str = ""
    text_only_completed: bool = False
    brain_completed: bool = False
    finalized: bool = False


class InteractionLatencyTracker:
    """記錄從收到文字到整段互動結束的耗時切面。"""

    def __init__(self):
        self._lock = Lock()
        self._traces: dict[str, InteractionTraceState] = {}
        self._completed_traces: dict[str, dict[str, object]] = {}

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

    def get_completed_trace(self, trace_id: str) -> dict[str, object] | None:
        with self._lock:
            completed = self._completed_traces.get(trace_id)
            if completed is None:
                return None
            return dict(completed)

    def abort(self, trace_id: str | None, reason: str):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.pop(trace_id, None)
            self._completed_traces.pop(trace_id, None)
        if state is None:
            return
        self._log(trace_id, f"追蹤已中止：{reason}")

    def mark_stt_speech_started(self, trace_id: str | None):
        self._record(trace_id, "stt_speech_started", "STT 偵測到開始說話", first_only=True)

    def mark_stt_speech_ended(self, trace_id: str | None):
        self._record(trace_id, "stt_speech_ended", "STT 偵測到停止說話", first_only=True)

    def mark_stt_finalized(self, trace_id: str | None, text: str):
        if not trace_id:
            return
        normalized = str(text or "").strip()
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            if normalized:
                state.input_text = normalized
            if "stt_finalized" in state.stages:
                return
            now = perf_counter()
            state.stages["stt_finalized"] = now
            preview = _preview_text(normalized) if normalized else "(空白)"
            state.notes["stt_finalized"] = f"STT finalized：{preview}"
            elapsed_ms = self._elapsed_from(state, now)
        if elapsed_ms is not None:
            self._log(trace_id, f"STT finalized：{preview} (+{elapsed_ms}ms)")

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

    def mark_token_visible(self, trace_id: str | None, token: str):
        normalized = str(token or "").strip()
        if not normalized:
            return
        self._record(
            trace_id,
            "first_token_visible",
            f"第一個可見 token：{_preview_text(normalized)}",
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
                state.notes["first_tts_enqueued"] = f"TTS 已排入佇列：{_preview_text(text)}"
                elapsed_ms = self._elapsed_ms(state, "first_tts_enqueued")
            else:
                elapsed_ms = self._elapsed_ms(state, "first_tts_enqueued")
        if elapsed_ms is not None:
            if chunk_index == 1:
                self._log(trace_id, f"TTS 已排入佇列 (+{elapsed_ms}ms)")
            else:
                self._log(trace_id, f"第{chunk_index}段 TTS 已排入佇列 (+{elapsed_ms}ms)")

    def mark_tts_expected(self, trace_id: str | None, text: str):
        if not trace_id:
            return
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None:
                return
            state.tts_expected += 1
            if "tts_expected" in state.stages:
                return
            now = perf_counter()
            state.stages["tts_expected"] = now
            state.notes["tts_expected"] = f"TTS 待輸出：{_preview_text(text)}"
            elapsed_ms = self._elapsed_from(state, now)
        if elapsed_ms is not None:
            self._log(trace_id, f"TTS 待輸出 (+{elapsed_ms}ms)")

    def mark_tts_stream_started(self, trace_id: str | None, reply_id: str, bytes_forwarded: int):
        detail = f"TTS 開始送入播放器，reply={reply_id[:8]}，bytes={bytes_forwarded}"
        self._record(trace_id, "first_tts_stream_started", detail, first_only=True)

    def mark_driver_started(self, trace_id: str | None, reply_id: str):
        detail = f"播放驅動開始接手音訊，reply={reply_id[:8]}"
        self._record(trace_id, "first_driver_started", detail, first_only=True)
        # 保留舊欄位語意，方便既有摘要與工具兼容
        self._record(trace_id, "first_tts_playback_started", detail, first_only=True)

    def mark_tts_playback_started(self, trace_id: str | None, reply_id: str):
        self.mark_driver_started(trace_id, reply_id)

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
            self._log(trace_id, f"TTS 已完成 (+{elapsed_ms}ms) {note}")
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
                "tts_expected": state.tts_expected,
                "tts_enqueued": state.tts_enqueued,
                "tts_finished": state.tts_finished,
                "tts_failures": state.tts_failures,
                "timeout_promoted": state.timeout_promoted,
                "fallback_triggered": state.fallback_triggered,
                "selected_tts_provider": state.selected_tts_provider,
                "text_only_completed": state.text_only_completed,
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
        required_tts = max(state.tts_expected, state.tts_enqueued)
        return state.tts_finished >= required_tts

    def _finalize(self, trace_id: str):
        with self._lock:
            state = self._traces.get(trace_id)
            if state is None or state.finalized:
                return
            state.finalized = True
            end_time = perf_counter()
            state.stages["interaction_completed"] = end_time
            state.notes["interaction_completed"] = "整段互動已完成"
            summary_payload = self._build_summary_payload(state)
            self._completed_traces[trace_id] = summary_payload
            summary = self._build_summary_text(summary_payload)
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

        first_output_stage = None
        for candidate in ("first_token_visible", "first_action_fragment", "first_text_fragment", "first_brain_output"):
            if candidate in state.stages:
                first_output_stage = candidate
                break

        add_delta("stt_tail", "stt_speech_ended", "stt_finalized")
        add_delta("brain_queue_wait", "brain_queued", "brain_started")
        if first_output_stage is not None:
            add_delta("llm_to_first_output", "brain_started", first_output_stage)
        add_delta("eos_to_first_action", "stt_speech_ended", "first_action_dispatched")
        add_delta("tts_startup", "first_tts_enqueued", "first_tts_stream_started")
        add_delta("tts_to_driver_start", "first_tts_stream_started", "first_driver_started")
        add_delta("eos_to_first_audio", "stt_speech_ended", "first_driver_started")
        add_delta("tts_tail", "first_tts_stream_started", "interaction_completed")
        add_delta("post_brain_tail", "brain_completed", "interaction_completed")
        add_delta("eos_to_complete", "stt_speech_ended", "interaction_completed")

        bottleneck_label = "n/a"
        bottleneck_ms = 0
        if stage_durations:
            bottleneck_label, bottleneck_ms = max(stage_durations, key=lambda item: item[1])

        total_ms = self._elapsed_ms(state, "interaction_completed") or 0
        milestones = []
        for stage in (
            "first_token_visible",
            "first_action_fragment",
            "first_text_fragment",
            "first_action_dispatched",
            "first_tts_stream_started",
            "first_driver_started",
            "first_tts_playback_started",
            "timeout_promoted",
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
        provider_label = state.selected_tts_provider or "unknown"

        missing_stt = [
            stage_name
            for stage_name in ("stt_speech_started", "stt_speech_ended", "stt_finalized")
            if stage_name not in state.stages
        ]
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
            "missing_stt_milestones": missing_stt,
            "failure_suffix": failure_suffix,
            "stage_parts": stage_parts,
            "legacy_audio_start_stage": "first_tts_playback_started",
            "driver_start_stage": "first_driver_started",
        }

    @staticmethod
    def _build_summary_text(summary_payload: dict[str, object]) -> str:
        stage_parts = list(summary_payload.get("stage_parts", []))
        milestones = list(summary_payload.get("milestones", []))
        failure_suffix = str(summary_payload.get("failure_suffix", ""))
        missing_stt = list(summary_payload.get("missing_stt_milestones", []))
        stt_suffix = ""
        if missing_stt:
            stt_suffix = f" | missing_stt={','.join(missing_stt)}"
        provider_suffix = (
            f" | provider={summary_payload.get('selected_tts_provider', 'unknown')}"
            f" fallback_triggered={summary_payload.get('fallback_triggered', False)}"
            f" text_only={summary_payload.get('text_only_completed', False)}"
        )
        legacy_suffix = (
            f" | driver_start={summary_payload.get('driver_start_stage')} "
            f"legacy_audio_start={summary_payload.get('legacy_audio_start_stage')}"
        )
        return (
            "互動完成摘要 "
            f"source={summary_payload.get('source', 'unknown')} "
            f"total={summary_payload.get('total_ms', 0)}ms | "
            f"stages: {'; '.join(stage_parts)} | "
            f"bottleneck={summary_payload.get('bottleneck_label', 'n/a')}({summary_payload.get('bottleneck_ms', 0)}ms) | "
            f"milestones: {'; '.join(milestones)}{failure_suffix}{stt_suffix}{provider_suffix}{legacy_suffix}"
        )

    @staticmethod
    def _log(trace_id: str, message: str):
        print(f"[ECHOES][TRACE][{trace_id}] {message}")
