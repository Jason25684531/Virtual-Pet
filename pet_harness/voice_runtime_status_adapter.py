from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


STATUS_CONFIGURED_AND_READY = "configured_and_ready"
STATUS_RUNTIME_AVAILABLE = "runtime_available"
STATUS_TRIGGER_NOT_WIRED = "runtime_present_trigger_not_wired"
STATUS_MISSING_RUNTIME = "configured_missing_runtime"
VALID_STATUS_VALUES = {
    STATUS_CONFIGURED_AND_READY,
    STATUS_RUNTIME_AVAILABLE,
    STATUS_TRIGGER_NOT_WIRED,
    STATUS_MISSING_RUNTIME,
}

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\b(token|api[_-]?key|authorization)\s*[:=]\s*[A-Za-z0-9._-]{6,}"),
)


@dataclass(frozen=True)
class VoiceStatusDTO:
    stt_status: str
    tts_primary_status: str
    tts_fallback_status: str
    audio_worker_status: str
    last_voice_error: str
    overall_status: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class VoiceRuntimeStatusAdapter:
    def __init__(self, stt_controller=None, tts_clients=None, audio_worker=None) -> None:
        self._stt_controller = stt_controller
        self._tts_clients = tts_clients
        self._audio_worker = audio_worker

    def get_status(self) -> VoiceStatusDTO:
        stt_status = self._status_for_stt(self._stt_controller)
        tts_primary_status = self._status_for_tts_client(self._extract_tts_client("primary"))
        tts_fallback_status = self._status_for_tts_client(self._extract_tts_client("fallback"))
        audio_worker_status = self._status_for_audio_worker(self._audio_worker)
        last_voice_error = self._mask_error(
            self._first_non_empty(
                self._read_error(self._stt_controller),
                self._read_error(self._extract_tts_client("primary")),
                self._read_error(self._extract_tts_client("fallback")),
                self._read_error(self._audio_worker),
            )
        )
        overall_status = self._coalesce_overall_status(
            stt_status,
            tts_primary_status,
            tts_fallback_status,
            audio_worker_status,
        )
        return VoiceStatusDTO(
            stt_status=stt_status,
            tts_primary_status=tts_primary_status,
            tts_fallback_status=tts_fallback_status,
            audio_worker_status=audio_worker_status,
            last_voice_error=last_voice_error,
            overall_status=overall_status,
        )

    def _extract_tts_client(self, key: str) -> Any:
        clients = self._tts_clients
        if isinstance(clients, dict):
            if key in clients:
                return clients.get(key)
            if key == "primary":
                return clients
            return None
        return clients if key == "primary" else None

    def _status_for_stt(self, controller: Any) -> str:
        if controller is None:
            return STATUS_MISSING_RUNTIME
        state = self._call_or_read(controller, "state")
        if state in {"idle", "starting", "listening", "stopping"}:
            return STATUS_CONFIGURED_AND_READY
        if bool(self._call_or_read(controller, "is_listening")):
            return STATUS_CONFIGURED_AND_READY
        if state == "unavailable":
            return STATUS_MISSING_RUNTIME
        return STATUS_RUNTIME_AVAILABLE

    def _status_for_tts_client(self, client: Any) -> str:
        if client is None:
            return STATUS_MISSING_RUNTIME
        if isinstance(client, dict):
            if client.get("wired") is False:
                return STATUS_TRIGGER_NOT_WIRED
            if client.get("ready") is True:
                return STATUS_CONFIGURED_AND_READY
            if client.get("configured") is True or client.get("available") is True or client.get("present") is True:
                return STATUS_RUNTIME_AVAILABLE
            return STATUS_RUNTIME_AVAILABLE
        if hasattr(client, "audio_ready_signal") or hasattr(client, "finished_signal"):
            return STATUS_RUNTIME_AVAILABLE
        return STATUS_RUNTIME_AVAILABLE

    def _status_for_audio_worker(self, worker: Any) -> str:
        if worker is None:
            return STATUS_MISSING_RUNTIME
        if hasattr(worker, "is_busy") or hasattr(worker, "start"):
            return STATUS_RUNTIME_AVAILABLE
        return STATUS_RUNTIME_AVAILABLE

    def _coalesce_overall_status(self, *statuses: str) -> str:
        unique_statuses = {status for status in statuses if status in VALID_STATUS_VALUES}
        if unique_statuses == {STATUS_MISSING_RUNTIME}:
            return STATUS_MISSING_RUNTIME
        if STATUS_TRIGGER_NOT_WIRED in unique_statuses:
            return STATUS_TRIGGER_NOT_WIRED
        if STATUS_CONFIGURED_AND_READY in unique_statuses:
            return STATUS_CONFIGURED_AND_READY
        if STATUS_RUNTIME_AVAILABLE in unique_statuses:
            return STATUS_RUNTIME_AVAILABLE
        return STATUS_MISSING_RUNTIME

    def _read_error(self, source: Any) -> str:
        if source is None:
            return ""
        if isinstance(source, dict):
            return str(source.get("last_error") or source.get("error") or "")
        for attr in ("last_voice_error", "last_error", "_last_error", "error", "_error"):
            value = self._call_or_read(source, attr)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _call_or_read(source: Any, attr: str) -> Any:
        if source is None or not hasattr(source, attr):
            return None
        value = getattr(source, attr)
        return value() if callable(value) else value

    @staticmethod
    def _first_non_empty(*values: str) -> str:
        for value in values:
            if str(value or "").strip():
                return str(value)
        return ""

    def _mask_error(self, message: str) -> str:
        masked = str(message or "")
        for pattern in _SECRET_PATTERNS:
            masked = pattern.sub("[REDACTED]", masked)
        return masked
