"""TTS and audio-session behavior mixed into MotionCoordinator."""

from __future__ import annotations

import inspect
import logging
import queue
from uuid import uuid4

from PyQt5.QtCore import QTimer

import config
from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker
from text_utils import sanitize_tts_text

LOGGER = logging.getLogger(__name__)


class TtsPlaybackMixin:
    def _synthesize_tts(self, message: str, tone: str, trace_id: str | None = None):
        if not self._tts_enabled or tone in {"warn", "error"}:
            return

        speech_text = sanitize_tts_text(message)
        if not speech_text:
            return

        normalized_trace_id = str(trace_id or "").strip()
        if normalized_trace_id in self._suppressed_traces:
            skipped_by_design = normalized_trace_id in self._tts_not_expected_traces
            suppressed_message = (
                "因 play_music fast path 依設計略過語音。"
                if skipped_by_design
                else "因 timeout_promoted 已抑制後續句段。"
            )
            reply_id = uuid4().hex
            self._on_tts_finished(
                reply_id,
                False,
                suppressed_message,
                {
                    "reply_id": reply_id,
                    "trace_id": normalized_trace_id,
                    "text": speech_text,
                    "suppressed": True,
                    "tts_skipped_by_design": skipped_by_design,
                },
            )
            return

        if not callable(self._tts_worker_factory):
            LOGGER.warning("[ECHOES] TTS worker factory 無效，已回退到 AdaptiveTTSFallbackWorker。")
            self._tts_worker_factory = AdaptiveTTSFallbackWorker

        reply_id = uuid4().hex
        self._pending_tts_chunks.put((reply_id, speech_text, trace_id))
        self._reply_texts[reply_id] = speech_text
        if normalized_trace_id:
            self._trace_pending_tts_counts[normalized_trace_id] = (
                self._trace_pending_tts_counts.get(normalized_trace_id, 0) + 1
            )
        pending_state = self._pending_actions.get(normalized_trace_id) if normalized_trace_id else None
        if pending_state is not None:
            pending_state.has_tts = True
        if self._current_loop_action_key is not None:
            self._loop_action_tts_queued = True
            if self._loop_cleanup_timer is not None:
                self._loop_cleanup_timer.stop()
                self._loop_cleanup_timer = None
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_enqueued(trace_id, reply_id, speech_text)
        self._start_next_tts_worker()

    def _start_next_tts_worker(self):
        if self._active_tts_worker is not None or self._pending_tts_chunks.empty():
            return

        reply_id, speech_text, trace_id = self._pending_tts_chunks.get_nowait()
        normalized_trace_id = str(trace_id or "").strip()
        if normalized_trace_id in self._suppressed_traces:
            skipped_by_design = normalized_trace_id in self._tts_not_expected_traces
            suppressed_message = (
                "因 play_music fast path 依設計略過語音。"
                if skipped_by_design
                else "因已改為文字-only 或 timeout_promoted，略過後續句段。"
            )
            self._on_tts_finished(
                reply_id,
                False,
                suppressed_message,
                {
                    "reply_id": reply_id,
                    "trace_id": normalized_trace_id,
                    "text": speech_text,
                    "suppressed": True,
                    "tts_skipped_by_design": skipped_by_design,
                },
            )
            self._start_next_tts_worker()
            return

        current_character_id = self._current_character_id()
        factory_name = getattr(self._tts_worker_factory, "__name__", "")
        preferred_provider = self._trace_tts_providers.get(normalized_trace_id, "")
        if factory_name in {"VoAIStreamingTTSWorker", "AdaptiveTTSFallbackWorker"}:
            voice_id = current_character_id or ""
        else:
            voice_id = config.get_elevenlabs_voice_id_for_character(current_character_id)

        worker_kwargs = {
            "text": speech_text,
            "reply_id": reply_id,
            "trace_id": trace_id,
            "voice_id": voice_id,
            "parent": self,
        }
        try:
            signature = inspect.signature(self._tts_worker_factory)
            if "playback_guard" in signature.parameters:
                worker_kwargs["playback_guard"] = self._can_start_trace_audio
            if "fallback_voice_id" in signature.parameters:
                worker_kwargs["fallback_voice_id"] = config.get_elevenlabs_voice_id_for_character(current_character_id)
            if "preferred_provider" in signature.parameters and preferred_provider:
                worker_kwargs["preferred_provider"] = preferred_provider
            if "resolved_tts_mode" in signature.parameters:
                resolved_mode, _ = config.resolve_tts_runtime_mode()
                worker_kwargs["resolved_tts_mode"] = resolved_mode
            if "pcm_stream_sink" in signature.parameters:
                worker_kwargs["pcm_stream_sink"] = self._audio_worker
        except (TypeError, ValueError):
            pass

        worker = self._tts_worker_factory(**worker_kwargs)
        self._active_tts_worker = worker
        self._workers.append(worker)

        def handle_audio_ready(audio_bytes, r_id: str, t_id: str):
            if t_id and t_id in self._suppressed_traces:
                return
            self._record_spoken_reply(r_id, t_id)
            self._audio_worker.enqueue(audio_bytes, r_id, t_id)

        def handle_result(success: bool, result_message: str, payload: object, current_reply_id=reply_id):
            self._on_tts_finished(current_reply_id, success, result_message, payload)

        def handle_progress(event_name: str, payload: object):
            self._on_tts_progress(event_name, payload)

        def on_thread_finished(current_worker=worker):
            # HTTP 取音訊完成（非播放完成），立即啟動下一個 HTTP 取音訊
            # AudioStreamWorker 負責按序播放，不再等待音訊播完才取下一句
            if current_worker in self._workers:
                self._workers.remove(current_worker)
            if self._active_tts_worker is current_worker:
                self._active_tts_worker = None
            if hasattr(current_worker, "deleteLater"):
                current_worker.deleteLater()
            self._start_next_tts_worker()
            self._finish_loop_action_if_tts_idle()

        if hasattr(worker, "audio_ready_signal"):
            worker.audio_ready_signal.connect(handle_audio_ready)
        worker.finished_signal.connect(handle_result)
        if hasattr(worker, "progress_signal"):
            worker.progress_signal.connect(handle_progress)
        worker.finished.connect(on_thread_finished)
        worker.start()

    def _on_audio_queue_drained(self):
        """AudioStreamWorker 播放佇列清空時觸發。用於 loop action 的 TTS 完成偵測。"""
        self._finish_loop_action_if_tts_idle()

    def _maybe_close_trace_audio_session(self, trace_id: str | None):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id or normalized_trace_id not in self._completed_tts_traces:
            return
        if self._trace_pending_tts_counts.get(normalized_trace_id, 0) > 0:
            return
        self._audio_worker.close_trace_session(normalized_trace_id)

    def _finish_loop_action_if_tts_idle(self):
        active_trace_id = str(self._active_action_trace_id or "").strip()
        if not (self._loop_action_tts_queued
                and self._current_loop_action_key is not None
                and self._pending_tts_chunks.empty()
                and self._active_tts_worker is None
                and not self._audio_worker.is_busy()
                and self._trace_pending_tts_counts.get(active_trace_id, 0) == 0
                and active_trace_id not in self._streaming_traces):
            return
        if self._wait_for_room_audio_ended or self._loop_action_service_pending:
            return
        current_binding = self._current_loop_binding
        if current_binding is None:
            self._finish_loop_action()
            return
        # panel video 尚未結束時，等待 JS 的 _on_panel_video_ended 回調再 finish
        if self._panel_video_started and not self._panel_video_ended:
            return
        if current_binding.finish_event == "main_video":
            self._wait_for_main_video_ended = True
            self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
            return
        self._finish_loop_action()

    def _on_panel_video_ended(self):
        """JS panel video 播畢時的 Python 回調（由 transparent_window 轉發）。"""
        self._panel_video_ended = True
        if self._current_loop_action_key is None:
            return
        if self._wait_for_room_audio_ended or self._loop_action_service_pending:
            return
        current_binding = self._current_loop_binding
        if current_binding is None:
            self._finish_loop_action()
            return
        tts_idle = (
            not self._loop_action_tts_queued
            or (self._pending_tts_chunks.empty()
                and self._active_tts_worker is None
                and not self._audio_worker.is_busy())
        )
        if tts_idle:
            if current_binding.finish_event == "panel_video":
                self._finish_loop_action()
                return
            if current_binding.finish_event == "main_video":
                self._wait_for_main_video_ended = True
                self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
                return
            self._finish_loop_action()

    def _on_main_video_ended(self):
        """JS main character video 播畢時的 Python 回調。"""
        if self._wait_for_room_audio_ended or self._loop_action_service_pending:
            return
        if not (self._wait_for_main_video_ended and self._current_loop_action_key is not None):
            return
        self._finish_loop_action()

    def _on_room_audio_ended(self):
        if not (self._wait_for_room_audio_ended and self._current_loop_action_key is not None):
            return
        self._wait_for_room_audio_ended = False
        current_binding = self._current_loop_binding
        if current_binding is not None and current_binding.finish_event == "main_video":
            self._wait_for_main_video_ended = True
            self._schedule_loop_cleanup(12000, wait_for_main_video_end=True)
            return
        self._finish_loop_action()

    def _on_tts_progress(self, event_name: str, payload: object):
        if not isinstance(payload, dict):
            return
        normalized_trace_id = str(payload.get("trace_id") or "").strip()
        if event_name == "provider_selected":
            provider = str(payload.get("provider") or "").strip()
            if normalized_trace_id and provider:
                self._trace_tts_providers[normalized_trace_id] = provider
            if self._latency_tracker is not None:
                self._latency_tracker.mark_tts_provider_selected(
                    normalized_trace_id,
                    provider,
                    str(payload.get("reason") or ""),
                )
            if payload.get("fallback_locked"):
                self._apply_fallback_timeout_grace(normalized_trace_id)
            return
        if event_name == "fallback_triggered":
            target_provider = str(payload.get("to_provider") or "elevenlabs").strip() or "elevenlabs"
            if normalized_trace_id:
                self._trace_tts_providers[normalized_trace_id] = target_provider
            if self._latency_tracker is not None:
                self._latency_tracker.mark_tts_fallback_triggered(
                    normalized_trace_id,
                    str(payload.get("from_provider") or "voai"),
                    target_provider,
                    str(payload.get("failure_code") or ""),
                )
            self._apply_fallback_timeout_grace(normalized_trace_id)
            return
        if event_name == "critical_tts_failure":
            if self._latency_tracker is not None:
                self._latency_tracker.mark_text_only_completed(
                    normalized_trace_id,
                    str(payload.get("provider_chain") or ""),
                )
            return
        if event_name == "stream_started" and self._latency_tracker is not None:
            self._latency_tracker.mark_tts_stream_started(
                normalized_trace_id,
                str(payload.get("reply_id", "")),
                int(payload.get("bytes_forwarded", payload.get("bytes_received", 0)) or 0),
            )
            return
        if event_name in {"driver_started", "playback_started"}:
            self._on_driver_started(
                str(payload.get("reply_id", "")),
                payload.get("trace_id"),
            )

    def _on_audio_driver_started(self, reply_id: str, trace_id: str):
        self._on_driver_started(reply_id, trace_id)

    def _on_audio_playback_finished(self, reply_id: str, trace_id: str):
        result = self._queued_playback_results.pop(reply_id, None)
        message = result[1] if result is not None else "語音播放完成。"
        normalized_trace_id = str(trace_id or "").strip()
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_finished(normalized_trace_id, reply_id, True, message)
        LOGGER.info("[ECHOES] 語音播放完成。%s", message)

    def _record_spoken_reply(self, reply_id: str, trace_id: str | None) -> None:
        if reply_id in self._spoken_reply_ids:
            return
        spoken_text = self._reply_texts.get(reply_id)
        if not spoken_text:
            return
        self._spoken_reply_ids.add(reply_id)
        record = getattr(self._window, "record_spoken_chunk", None)
        if callable(record):
            record(str(trace_id or "").strip(), spoken_text)

    def _on_driver_started(self, reply_id: str, trace_id: str | None):
        if not reply_id:
            return
        normalized_trace_id = str(trace_id or "").strip()
        event_key = (reply_id, normalized_trace_id)
        if event_key in self._driver_started_pairs:
            return
        self._driver_started_pairs.add(event_key)
        self._driver_started_replies.add(reply_id)
        self._record_spoken_reply(reply_id, normalized_trace_id)
        if self._latency_tracker is not None:
            self._latency_tracker.mark_driver_started(normalized_trace_id, reply_id)
        if normalized_trace_id in self._suppressed_traces:
            return
        self._activate_pending_action(normalized_trace_id, promoted=False)

    def reset_runtime_state(self):
        for trace_id in list(self._pending_actions):
            self._clear_pending_action(trace_id)
        self._pending_actions.clear()
        self._suppressed_traces.clear()
        self._tts_not_expected_traces.clear()
        self._driver_started_replies.clear()
        self._driver_started_pairs.clear()
        self._queued_playback_results.clear()
        self._trace_tts_providers.clear()
        self._reply_texts.clear()
        self._spoken_reply_ids.clear()
        self._trace_pending_tts_counts.clear()
        self._completed_tts_traces.clear()
        self._streaming_traces.clear()
        self._deferred_dispatches.clear()
        while not self._pending_tts_chunks.empty():
            try:
                self._pending_tts_chunks.get_nowait()
            except queue.Empty:
                break
        if self._loop_cleanup_timer is not None:
            self._loop_cleanup_timer.stop()
            self._loop_cleanup_timer = None
        self._audio_worker.clear_queue()
        active_worker = self._active_tts_worker
        if active_worker is not None and hasattr(active_worker, "quit"):
            try:
                active_worker.quit()
            except Exception:
                LOGGER.debug("TTS worker quit failed", exc_info=True)
        self._active_tts_worker = None
        self._current_loop_action_key = None
        self._current_loop_binding = None
        self._loop_action_tts_queued = False
        self._active_action_trace_id = None
        self._wait_for_main_video_ended = False
        self._wait_for_room_audio_ended = False
        self._loop_action_service_pending = False
        self._panel_video_started = False
        self._panel_video_ended = False

    def _on_tts_finished(self, reply_id: str, success: bool, message: str, payload: object):
        trace_id = payload.get("trace_id") if isinstance(payload, dict) else None
        normalized_trace_id = str(trace_id or "").strip()
        skipped_by_design = bool(isinstance(payload, dict) and payload.get("tts_skipped_by_design"))
        if normalized_trace_id:
            pending_count = self._trace_pending_tts_counts.get(normalized_trace_id, 0)
            if pending_count <= 1:
                self._trace_pending_tts_counts.pop(normalized_trace_id, None)
            else:
                self._trace_pending_tts_counts[normalized_trace_id] = pending_count - 1
            # 標記同輪最後一個 TTS producer 已完成；後續各結果分支會統一
            # 關閉 PCM session。session 關閉後 AudioStreamWorker 才會發出
            # queue_drained，讓角色動作回到 idle。
            if self._trace_pending_tts_counts.get(normalized_trace_id, 0) == 0:
                self._completed_tts_traces.add(normalized_trace_id)
        if normalized_trace_id in self._suppressed_traces and reply_id not in self._driver_started_replies and not skipped_by_design:
            success = False
            if "抑制" not in message:
                message = "因 timeout_promoted 抑制晚到音訊。"
        if isinstance(payload, dict):
            selected_provider = str(payload.get("selected_provider") or payload.get("provider") or "").strip()
            if normalized_trace_id and selected_provider:
                self._trace_tts_providers[normalized_trace_id] = selected_provider
            if payload.get("critical_tts_failure"):
                self._handle_critical_tts_failure(normalized_trace_id, message)
        if skipped_by_design and self._latency_tracker is not None:
            self._latency_tracker.mark_tts_skipped_by_design(normalized_trace_id, message)
        queued_playback = bool(isinstance(payload, dict) and payload.get("queued_playback"))
        if queued_playback and success:
            self._queued_playback_results[reply_id] = (trace_id, message)
            self._maybe_close_trace_audio_session(normalized_trace_id)
            return
        if self._latency_tracker is not None:
            self._latency_tracker.mark_tts_finished(
                trace_id,
                reply_id,
                success,
                message,
                skipped_by_design=skipped_by_design,
            )
        if not success and not skipped_by_design:
            LOGGER.warning("[ECHOES] 串流 TTS 未播放，保留文字回覆。%s", message)
            pending_state = self._pending_actions.get(normalized_trace_id)
            if (
                pending_state is not None
                and pending_state.wait_for_tts_start
                and self._trace_pending_tts_counts.get(normalized_trace_id, 0) == 0
            ):
                self._clear_pending_action(normalized_trace_id)
                self._window.restore_idle_video()
            self._maybe_close_trace_audio_session(normalized_trace_id)
            return
        LOGGER.info("[ECHOES] 語音播放完成。%s", message)
        self._maybe_close_trace_audio_session(normalized_trace_id)

    def _apply_fallback_timeout_grace(self, trace_id: str | None):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        state = self._pending_actions.get(normalized_trace_id)
        if state is None or state.status != "pending" or state.fallback_grace_applied:
            return
        timer = state.timeout_timer
        if timer is None:
            return
        remaining_ms = max(0, int(timer.remainingTime()))
        timer.start(remaining_ms + self._fallback_timeout_grace_ms)
        state.fallback_grace_applied = True

    def _handle_critical_tts_failure(self, trace_id: str | None, message: str):
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return
        self._suppressed_traces.add(normalized_trace_id)
        self._audio_worker.suppress_trace(normalized_trace_id)
        self._clear_pending_action(normalized_trace_id)
        if self._active_action_trace_id == normalized_trace_id and self._current_loop_action_key is not None:
            self._finish_loop_action()
            self._suppressed_traces.add(normalized_trace_id)
            self._audio_worker.suppress_trace(normalized_trace_id)
        else:
            self._window.restore_idle_video()
        self._window.set_action_status(
            message or "語音服務暫時失敗，已保留文字回覆。",
            tone="warn",
            timeout_ms=4200,
        )

    def _can_start_trace_audio(self, trace_id: str | None, _reply_id: str | None = None) -> bool:
        normalized_trace_id = str(trace_id or "").strip()
        return normalized_trace_id not in self._suppressed_traces

    def shutdown(self, wait_ms: int = 5000):
        if self._news_audio_delay_timer is not None:
            self._news_audio_delay_timer.stop()
            self._news_audio_delay_timer = None
        for trace_id in list(self._pending_actions):
            self._clear_pending_action(trace_id)
        self._pending_actions.clear()
        self._suppressed_traces.clear()
        self._tts_not_expected_traces.clear()
        self._driver_started_replies.clear()
        self._driver_started_pairs.clear()
        self._queued_playback_results.clear()
        self._trace_tts_providers.clear()
        self._reply_texts.clear()
        self._spoken_reply_ids.clear()
        self._trace_pending_tts_counts.clear()
        self._completed_tts_traces.clear()
        self._streaming_traces.clear()
        self._deferred_dispatches.clear()
        while not self._pending_tts_chunks.empty():
            try:
                self._pending_tts_chunks.get_nowait()
            except queue.Empty:
                break

        # 停止 AudioStreamWorker（清空佇列後送 sentinel 正常退出）
        self._audio_worker.clear_queue()
        self._audio_worker.stop()
        self._audio_worker.wait(wait_ms)

        workers = list(self._workers)
        active_worker = self._active_tts_worker
        if active_worker is not None and active_worker not in workers:
            workers.append(active_worker)

        for worker in workers:
            try:
                if hasattr(worker, "quit"):
                    worker.quit()
            except Exception:
                LOGGER.debug("TTS worker quit failed during shutdown", exc_info=True)

            is_running = getattr(worker, "isRunning", None)
            wait = getattr(worker, "wait", None)
            terminate = getattr(worker, "terminate", None)
            if callable(is_running) and callable(wait) and is_running():
                if not wait(wait_ms) and callable(terminate):
                    try:
                        terminate()
                        wait(1000)
                    except Exception:
                        LOGGER.warning("TTS worker terminate failed", exc_info=True)

        self._active_tts_worker = None
        self._current_loop_binding = None
        self._workers = []

