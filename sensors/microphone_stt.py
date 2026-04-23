"""
ECHOES — Azure 麥克風背景語音辨識 worker。

使用 Azure Speech SDK 在背景執行緒中持續監聽預設麥克風，
僅於 finalized `Recognized` 事件取得非空文字時透過 Qt signal 對外發送。
"""

from __future__ import annotations

import threading
from typing import Any

from PyQt5.QtCore import QThread, pyqtSignal

import config

try:
    import azure.cognitiveservices.speech as speechsdk
    AZURE_SPEECH_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - 依安裝環境決定
    speechsdk = None  # type: ignore[assignment]
    AZURE_SPEECH_IMPORT_ERROR = exc


def _log_stt(message: str):
    print(f"[ECHOES][STT] {message}")


class AzureSTTWorker(QThread):
    """在背景執行緒中執行 Azure 連續語音辨識。"""

    recognized_text = pyqtSignal(str)
    warning_emitted = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    listening_state_changed = pyqtSignal(bool)

    def __init__(
        self,
        api_key: str | None = None,
        region: str | None = None,
        language: str | None = None,
        speech_sdk: Any | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._speech_sdk = speech_sdk if speech_sdk is not None else speechsdk
        self._api_key = (api_key if api_key is not None else config.AZURE_STT_API_KEY).strip()
        self._region = (region if region is not None else config.AZURE_STT_REGION).strip()
        self._language = (language if language is not None else config.AZURE_STT_LANGUAGE).strip()
        self._stop_requested = threading.Event()
        self._recognizer: Any | None = None

    def stop(self):
        """通知 worker 停止 continuous recognition。"""

        self._stop_requested.set()
        _log_stt("收到停止請求，準備結束背景辨識。")

    def run(self):
        self._stop_requested.clear()

        if self._speech_sdk is None:
            package_name = "azure-cognitiveservices-speech"
            self._emit_warning(f"Azure STT 未啟用：尚未安裝 `{package_name}`。")
            self.listening_state_changed.emit(False)
            return

        if not self._api_key or not self._region:
            self._emit_warning("Azure STT 未啟用：缺少 AZURE_STT_API_KEY 或 AZURE_STT_REGION。")
            self.listening_state_changed.emit(False)
            return

        if not self._language:
            self._language = config.DEFAULT_AZURE_STT_LANGUAGE

        try:
            recognizer = self._create_recognizer()
            self._recognizer = recognizer
            self._bind_events(recognizer)

            self._emit_status(
                f"Azure STT 啟動中，語系 {self._language}，region {self._region}。"
            )
            _log_stt(
                f"開始 continuous recognition。language={self._language}, region={self._region}"
            )
            recognizer.start_continuous_recognition_async().get()

            while not self._stop_requested.wait(0.1):
                continue
        except Exception as exc:  # pragma: no cover - 依外部 SDK 與設備而定
            self._emit_warning(f"Azure STT 啟動失敗：{exc}")
        finally:
            self._stop_recognition()
            self._recognizer = None
            self.listening_state_changed.emit(False)
            _log_stt("背景辨識流程已結束。")

    def _create_recognizer(self):
        speech_config = self._speech_sdk.SpeechConfig(
            subscription=self._api_key,
            region=self._region,
        )
        speech_config.speech_recognition_language = self._language
        audio_config = self._speech_sdk.audio.AudioConfig(use_default_microphone=True)
        return self._speech_sdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

    def _bind_events(self, recognizer):
        recognizer.recognized.connect(self._handle_recognized_event)
        recognizer.canceled.connect(self._handle_canceled_event)
        recognizer.session_started.connect(self._handle_session_started_event)
        recognizer.session_stopped.connect(self._handle_session_stopped_event)

    def _stop_recognition(self):
        recognizer = self._recognizer
        if recognizer is None:
            return

        try:
            recognizer.stop_continuous_recognition_async().get()
            _log_stt("continuous recognition 已停止。")
        except Exception as exc:  # pragma: no cover - 依外部 SDK 與設備而定
            _log_stt(f"停止 continuous recognition 時發生例外：{exc}")

    def _handle_session_started_event(self, _event):
        self._emit_status("Azure STT 已開始接收麥克風音訊。")
        self.listening_state_changed.emit(True)
        _log_stt("語音工作階段開始。")

    def _handle_session_stopped_event(self, _event):
        self._emit_status("Azure STT 工作階段已停止。")
        self.listening_state_changed.emit(False)
        _log_stt("語音工作階段停止。")
        self._stop_requested.set()

    def _handle_canceled_event(self, event):
        reason = getattr(event, "reason", None)
        error_details = str(getattr(event, "error_details", "") or "").strip()
        details = error_details or str(reason or "未知原因")
        self._emit_warning(f"Azure STT 已取消：{details}")
        self.listening_state_changed.emit(False)
        self._stop_requested.set()

    def _handle_recognized_event(self, event):
        result = getattr(event, "result", None)
        if result is None:
            _log_stt("收到空的 Recognized 事件，已忽略。")
            return

        result_reason = getattr(result, "reason", None)
        recognized_reason = getattr(
            getattr(self._speech_sdk, "ResultReason", None),
            "RecognizedSpeech",
            None,
        )
        no_match_reason = getattr(
            getattr(self._speech_sdk, "ResultReason", None),
            "NoMatch",
            None,
        )

        if recognized_reason is not None and result_reason != recognized_reason:
            if no_match_reason is not None and result_reason == no_match_reason:
                _log_stt("Azure STT 回傳 NoMatch，未發送至 BrainEngine。")
            else:
                _log_stt(f"忽略非 RecognizedSpeech 結果：{result_reason}")
            return

        self._emit_recognized_text(getattr(result, "text", ""))

    def _emit_recognized_text(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            _log_stt("忽略空白辨識結果。")
            return False

        preview = normalized if len(normalized) <= 32 else f"{normalized[:32]}..."
        _log_stt(f"觸發 Recognized：{preview}")
        self.recognized_text.emit(normalized)
        return True

    def _emit_warning(self, message: str):
        _log_stt(message)
        self.warning_emitted.emit(message)

    def _emit_status(self, message: str):
        _log_stt(message)
        self.status_changed.emit(message)
