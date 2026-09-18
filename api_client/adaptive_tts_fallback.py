"""
Adaptive multi-provider TTS orchestration for ECHOES.

編排層本身不認識任何具體供應商:它只依序走過一份 TtsProvider 鏈,每個供應商
自己的名字、失敗後要不要換下一個、換下一個的原因怎麼取,都是鏈上每個
TtsProvider 描述帶的資料(見 api_client/tts_contract.py)。新增或移除一個
供應商只需要改 build_tts_provider_chain,這個檔案不需要跟著動。
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QObject, pyqtSignal

from api_client.tts_contract import TtsProvider, TtsRequest, build_tts_provider_chain

LOGGER = logging.getLogger(__name__)


class AdaptiveTTSFallbackWorker(QObject):
    """依序嘗試一份供應商鏈,直到成功或全數失敗，統一封裝在單一 worker 契約下。"""

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)
    finished = pyqtSignal()

    def __init__(
        self,
        request: TtsRequest,
        parent=None,
        *,
        chain: tuple[TtsProvider, ...] | None = None,
    ):
        super().__init__(parent)
        self._request = request
        self._chain = tuple(chain) if chain is not None else build_tts_provider_chain(
            request.character_id, request.preferred_provider
        )
        self._running = False
        self._chain_index = -1
        self._active_worker = None
        self._workers: list[object] = []
        self._provider_chain: list[str] = []
        self._fallback_reasons: list[tuple[str, str]] = []
        self._final_result_ready = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._advance(reason="initial")

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

    def _advance(self, *, reason: str):
        self._chain_index += 1
        provider = self._chain[self._chain_index]
        self._provider_chain.append(provider.name)
        self.progress_signal.emit(
            "provider_selected",
            {
                "reply_id": self._request.reply_id,
                "trace_id": self._request.trace_id,
                "provider": provider.name,
                "reason": reason,
                "fallback_locked": provider.needs_fallback_timeout_grace and reason != "initial",
            },
        )
        worker = provider.factory(self._request, parent=self)
        self._active_worker = worker
        self._workers.append(worker)
        self._wire_worker(worker, provider.name)
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
        normalized_payload.setdefault("reply_id", self._request.reply_id)
        normalized_payload.setdefault("trace_id", self._request.trace_id)
        normalized_payload.setdefault("provider", provider)
        normalized_payload.setdefault("selected_provider", provider)
        normalized_payload.setdefault("requested_mode", self._chain[0].name)
        normalized_payload.setdefault("resolved_mode", self._request.resolved_tts_mode)
        normalized_payload.setdefault("attempted_providers", list(self._provider_chain))

        if success:
            normalized_payload["outcome"] = "success"
            self._finish(success, message, normalized_payload)
            return

        current_provider = self._chain[self._chain_index]
        has_next = self._chain_index + 1 < len(self._chain)
        should_advance = has_next and current_provider.should_advance_on_failure(normalized_payload)

        if should_advance:
            next_provider = self._chain[self._chain_index + 1]
            fallback_reason = current_provider.fallback_reason(message, normalized_payload)
            self._fallback_reasons.append((provider, fallback_reason))
            fallback_payload = dict(normalized_payload)
            fallback_payload.update(
                {
                    "from_provider": provider,
                    "to_provider": next_provider.name,
                    "fallback_reason": fallback_reason,
                    "fallback_reasons": list(self._fallback_reasons),
                }
            )
            self.progress_signal.emit("fallback_triggered", fallback_payload)
            LOGGER.warning(
                "[ECHOES] %s 失敗改用 %s：character=%s reason=%s text_len=%d",
                provider,
                next_provider.name,
                self._request.character_id,
                fallback_reason,
                len(self._request.text),
            )
            self._advance(reason=fallback_reason)
            return

        if len(self._provider_chain) > 1:
            normalized_payload.update(
                {
                    "critical_tts_failure": True,
                    "text_only": True,
                    "provider_chain": list(self._provider_chain),
                    "fallback_reasons": list(self._fallback_reasons),
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
