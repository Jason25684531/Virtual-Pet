"""
Adaptive multi-provider TTS orchestration for ECHOES.
"""

from __future__ import annotations

import inspect
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal

import config
from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker
from api_client.voai_client import VoAIStreamingTTSWorker


def _normalize_provider_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"voai", "elevenlabs"}:
        return normalized
    return ""


class AdaptiveTTSFallbackWorker(QObject):
    """Wrap VoAI primary + ElevenLabs fallback behind one worker contract."""

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)
    finished = pyqtSignal()

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
        preferred_provider: str | None = None,
        playback_guard=None,
        pcm_stream_sink=None,
        voai_worker_factory: Callable[..., object] | None = None,
        elevenlabs_worker_factory: Callable[..., object] | None = None,
        resolved_tts_mode: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._text = str(text or "").strip()
        self._reply_id = str(reply_id or "").strip()
        self._trace_id = str(trace_id or "").strip()
        self._character_id = str(voice_id or "").strip()
        self._fallback_voice_id = (
            str(fallback_voice_id or "").strip()
            or config.get_elevenlabs_voice_id_for_character(self._character_id)
        )
        self._preferred_provider = _normalize_provider_name(preferred_provider) or "voai"
        self._playback_guard = playback_guard
        self._pcm_stream_sink = pcm_stream_sink
        self._voai_worker_factory = voai_worker_factory or VoAIStreamingTTSWorker
        self._elevenlabs_worker_factory = elevenlabs_worker_factory or ElevenLabsStreamingTTSWorker
        self._resolved_tts_mode = str(resolved_tts_mode or "voai_first").strip()
        self._running = False
        self._active_provider = ""
        self._active_worker = None
        self._workers: list[object] = []
        self._provider_chain: list[str] = []
        self._fallback_reasons: list[tuple[str, str]] = []
        self._final_result_ready = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._start_provider(self._preferred_provider, reason="initial")

    def isRunning(self):  # noqa: N802 - 保持與 QThread 介面相容
        return self._running

    def quit(self):
        worker = self._active_worker
        if worker is None:
            return
        quit_method = getattr(worker, "quit", None)
        if callable(quit_method):
            quit_method()

    def wait(self, timeout_ms: int = 5000) -> bool:
        worker = self._active_worker
        if worker is None:
            return True
        wait_method = getattr(worker, "wait", None)
        if callable(wait_method):
            return bool(wait_method(timeout_ms))
        return not self._running

    def _start_provider(self, provider: str, *, reason: str):
        normalized_provider = _normalize_provider_name(provider) or "voai"
        self._active_provider = normalized_provider
        self._provider_chain.append(normalized_provider)
        self.progress_signal.emit(
            "provider_selected",
            {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "provider": normalized_provider,
                "reason": reason,
                "fallback_locked": normalized_provider == "elevenlabs" and reason != "initial",
            },
        )
        if normalized_provider == "elevenlabs":
            worker_kwargs = {
                "text": self._text,
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "voice_id": self._fallback_voice_id,
                "parent": self,
            }
            try:
                signature = inspect.signature(self._elevenlabs_worker_factory)
                if "pcm_stream_sink" in signature.parameters:
                    worker_kwargs["pcm_stream_sink"] = self._pcm_stream_sink
            except (TypeError, ValueError):
                pass
            worker = self._elevenlabs_worker_factory(**worker_kwargs)
        else:
            worker_kwargs = {
                "text": self._text,
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "voice_id": self._character_id,
                "playback_guard": self._playback_guard,
                "adaptive_fallback_enabled": True,
                "parent": self,
            }
            try:
                signature = inspect.signature(self._voai_worker_factory)
                if "pcm_stream_sink" in signature.parameters:
                    worker_kwargs["pcm_stream_sink"] = self._pcm_stream_sink
            except (TypeError, ValueError):
                pass
            worker = self._voai_worker_factory(**worker_kwargs)
        self._active_worker = worker
        self._workers.append(worker)
        self._wire_worker(worker, normalized_provider)
        worker.start()

    def _wire_worker(self, worker, provider: str):
        if hasattr(worker, "audio_ready_signal"):
            worker.audio_ready_signal.connect(self.audio_ready_signal.emit)
        if hasattr(worker, "progress_signal"):
            worker.progress_signal.connect(
                lambda event_name, payload, current_provider=provider: self._forward_progress(
                    event_name,
                    payload,
                    current_provider,
                )
            )
        worker.finished_signal.connect(
            lambda success, message, payload, current_provider=provider: self._handle_result(
                success,
                message,
                payload,
                current_provider,
            )
        )
        worker.finished.connect(lambda current_worker=worker: self._cleanup_worker(current_worker))

    def _forward_progress(self, event_name: str, payload: object, provider: str):
        if isinstance(payload, dict):
            forwarded_payload = dict(payload)
            forwarded_payload.setdefault("provider", provider)
            self.progress_signal.emit(event_name, forwarded_payload)
            return
        self.progress_signal.emit(event_name, payload)

    def _handle_result(self, success: bool, message: str, payload: object, provider: str):
        normalized_payload = dict(payload) if isinstance(payload, dict) else {}
        normalized_payload.setdefault("reply_id", self._reply_id)
        normalized_payload.setdefault("trace_id", self._trace_id)
        normalized_payload.setdefault("provider", provider)
        normalized_payload.setdefault("selected_provider", provider)
        normalized_payload.setdefault("requested_mode", self._preferred_provider)
        normalized_payload.setdefault("resolved_mode", self._resolved_tts_mode)
        normalized_payload.setdefault("attempted_providers", list(self._provider_chain))

        if success:
            normalized_payload["outcome"] = "success"
            self._finish(success, message, normalized_payload)
            return

        if provider == "voai" and normalized_payload.get("fast_fail"):
            fallback_reason = normalized_payload.get("fast_fail", "unknown")
            self._fallback_reasons.append(("voai", str(fallback_reason)))
            fallback_payload = dict(normalized_payload)
            fallback_payload.update(
                {
                    "from_provider": "voai",
                    "to_provider": "elevenlabs",
                    "fallback_reason": fallback_reason,
                    "fallback_reasons": self._fallback_reasons,
                }
            )
            self.progress_signal.emit("fallback_triggered", fallback_payload)
            self._start_provider("elevenlabs", reason="fast_fail")
            return

        if provider == "elevenlabs" and "voai" in self._provider_chain:
            normalized_payload.update(
                {
                    "critical_tts_failure": True,
                    "text_only": True,
                    "provider_chain": list(self._provider_chain),
                    "fallback_reasons": self._fallback_reasons,
                    "outcome": "all_providers_failed",
                }
            )
            self.progress_signal.emit("critical_tts_failure", normalized_payload)

        normalized_payload.setdefault("outcome", "provider_failed")
        self._finish(False, message, normalized_payload)

    def _finish(self, success: bool, message: str, payload: dict):
        if not self._running:
            return
        self._running = False
        self._final_result_ready = True
        self.finished_signal.emit(success, message, payload)

    def _cleanup_worker(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)
        if self._active_worker is worker:
            self._active_worker = None
        if self._final_result_ready and not self._running and self._active_worker is None:
            self._final_result_ready = False
            self.finished.emit()
        if hasattr(worker, "deleteLater"):
            worker.deleteLater()
