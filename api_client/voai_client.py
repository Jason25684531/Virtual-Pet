"""
ECHOES — VoAI TTS 整合。

以 VoAI TTS API（https://connect.voai.ai/TTS/Speech）優先取得 PCM 串流音訊，
必要時回退 MP3 音訊並透過 audio_ready_signal 通知 AudioStreamWorker 播放。
介面與 ElevenLabsStreamingTTSWorker 相容，可直接替換。
"""

from __future__ import annotations

import io
import os
from uuid import uuid4

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config
from audio_playback import FfplayPcmAudioPlayer, PlaybackStartSuppressed

_VOAI_TTS_URL = "https://connect.voai.ai/TTS/Speech"
_VOAI_HTTP_SESSION = requests.Session()
_DEFAULT_STYLE = "預設"
_DEFAULT_SPEED = 1.2
_DEFAULT_STYLE_WEIGHT = 0.0
_DEFAULT_BREATH_PAUSE = 0.0
_PCM_SAMPLE_RATE = 32000


def _get_api_key() -> str:
    return (
        os.getenv("VOAI_API_KEY", "").strip()
        or os.getenv("VoAI_API_KEY", "").strip()
    )


def _classify_fast_fail(exc: Exception) -> tuple[str, str, bool]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 529:
        return "http_529", "VoAI concurrency limit exceeded", True
    if isinstance(exc, requests.ConnectionError):
        return "connection_error", "VoAI connection failed before playback", True
    return "request_error", str(exc), False


class VoAIStreamingTTSWorker(QThread):
    """呼叫 VoAI TTS API，優先 PCM 即時播放，必要時回退 MP3 佇列播放。

    介面與 ElevenLabsStreamingTTSWorker 相容：
      - 相同建構子參數：text, reply_id, trace_id, voice_id, parent
      - 相同 signals：finished_signal, progress_signal, audio_ready_signal
    """

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    # (BytesIO audio_buffer, reply_id, trace_id)
    audio_ready_signal = pyqtSignal(object, str, str)

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        requests_post=None,
        pcm_player_factory=None,
        playback_guard=None,
        adaptive_fallback_enabled: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._text = str(text or "").strip()
        self._reply_id = (reply_id or uuid4().hex).strip()
        self._trace_id = (trace_id or "").strip()
        self._voice_id = (voice_id or "").strip()
        self._requests_post = requests_post or _VOAI_HTTP_SESSION.post
        self._pcm_player_factory = pcm_player_factory or (
            lambda: FfplayPcmAudioPlayer(sample_rate=_PCM_SAMPLE_RATE, channels=1)
        )
        self._playback_guard = playback_guard
        self._adaptive_fallback_enabled = bool(adaptive_fallback_enabled)

    def run(self):
        if not self._text:
            self.finished_signal.emit(False, "略過 VoAI TTS：沒有可朗讀的文字。", self._build_result_payload())
            return

        api_key = _get_api_key()
        if not api_key:
            self.finished_signal.emit(False, "略過 VoAI TTS：缺少 VOAI_API_KEY。", self._build_result_payload())
            return

        voice_cfg = config.get_voai_config_for_character(self._voice_id)
        payload = self._build_payload(voice_cfg)
        if self._pcm_streaming_enabled():
            ok, fallback_reason, result_payload = self._try_pcm_stream(api_key, payload)
            if ok:
                return
            if isinstance(result_payload, dict) and result_payload.get("fast_fail") and self._adaptive_fallback_enabled:
                self.finished_signal.emit(False, fallback_reason, result_payload)
                return
            print(f"[ECHOES] 提示: VoAI PCM 串流不可用，改用 MP3 fallback。{fallback_reason}")

        self._run_mp3_fallback(api_key, payload)

    def _build_payload(self, voice_cfg: dict) -> dict:
        return {
            "version": voice_cfg.get("version", "Classic"),
            "text": self._text,
            "speaker": voice_cfg.get("speaker", "柔洢"),
            "style": voice_cfg.get("style", _DEFAULT_STYLE),
            "speed": float(voice_cfg.get("speed", _DEFAULT_SPEED)),
            "pitch_shift": float(voice_cfg.get("pitch_shift", 0)),
            "style_weight": float(voice_cfg.get("style_weight", _DEFAULT_STYLE_WEIGHT)),
            "breath_pause": float(voice_cfg.get("breath_pause", _DEFAULT_BREATH_PAUSE)),
        }

    def _build_result_payload(self, **extra) -> dict:
        payload = {
            "reply_id": self._reply_id,
            "trace_id": self._trace_id,
            "text": self._text,
            "provider": "voai",
        }
        payload.update(extra)
        return payload

    def _build_fast_fail_payload(
        self,
        *,
        stage: str,
        reason_code: str,
        detail: str,
        audio_format: str,
    ) -> dict:
        return self._build_result_payload(
            fast_fail=True,
            definitive=True,
            stage=stage,
            failure_code=reason_code,
            failure_detail=detail,
            format=audio_format,
        )

    @staticmethod
    def _pcm_streaming_enabled() -> bool:
        value = os.getenv("VOAI_PCM_STREAMING_ENABLED", "true").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _try_pcm_stream(self, api_key: str, payload: dict) -> tuple[bool, str, dict | None]:
        player = self._pcm_player_factory()
        is_available = getattr(player, "is_available", None)
        if callable(is_available) and not is_available():
            return False, "ffplay backend unavailable", None

        headers = {
            "x-api-key": api_key,
            "x-output-format": "pcm",
            "x-sample-rate": str(_PCM_SAMPLE_RATE),
            "Content-Type": "application/json",
        }

        response = None
        bytes_forwarded = 0
        try:
            response = self._requests_post(
                _VOAI_TTS_URL,
                headers=headers,
                json=payload,
                timeout=(5, 45),
                stream=True,
            )
            response.raise_for_status()
            content_type = str(response.headers.get("content-type", "") or "").lower()
            if "audio" not in content_type and "octet-stream" not in content_type:
                return False, f"VoAI PCM 回傳非音訊格式：{content_type}", None

            def iter_chunks():
                nonlocal bytes_forwarded
                for chunk in response.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    if bytes_forwarded <= 0:
                        self.progress_signal.emit(
                            "stream_started",
                            {
                                "reply_id": self._reply_id,
                                "trace_id": self._trace_id,
                                "bytes_forwarded": len(chunk),
                                "format": "pcm",
                            },
                        )
                    bytes_forwarded += len(chunk)
                    yield chunk

            def before_start():
                if callable(self._playback_guard) and self._playback_guard(self._trace_id, self._reply_id) is False:
                    return False
                payload = {
                    "reply_id": self._reply_id,
                    "trace_id": self._trace_id,
                    "format": "pcm",
                }
                self.progress_signal.emit("driver_started", payload)
                self.progress_signal.emit("playback_started", payload)
                return True

            played_bytes = int(player.play_chunks(iter_chunks(), before_start=before_start) or 0)
            if bytes_forwarded <= 0 and played_bytes <= 0:
                return False, "VoAI PCM 回傳空音訊。", None

            self.finished_signal.emit(
                True,
                "VoAI PCM 串流播放完成。",
                {
                    "reply_id": self._reply_id,
                    "trace_id": self._trace_id,
                    "text": self._text,
                    "bytes_forwarded": bytes_forwarded or played_bytes,
                    "format": "pcm",
                    "selected_provider": "voai",
                },
            )
            return True, "", None
        except PlaybackStartSuppressed:
            self.finished_signal.emit(
                False,
                "VoAI PCM 起播前已被同步策略抑制。",
                {
                    "reply_id": self._reply_id,
                    "trace_id": self._trace_id,
                    "text": self._text,
                    "format": "pcm",
                    "suppressed": True,
                },
            )
            return True, "", None
        except requests.RequestException as exc:
            reason_code, detail, definitive = _classify_fast_fail(exc)
            payload = None
            if definitive:
                payload = self._build_fast_fail_payload(
                    stage="pcm",
                    reason_code=reason_code,
                    detail=detail,
                    audio_format="pcm",
                )
            return False, f"VoAI PCM 網路錯誤: {exc}", payload
        except Exception as exc:
            return False, f"VoAI PCM 播放失敗: {exc}", None
        finally:
            if response is not None:
                response.close()

    def _run_mp3_fallback(self, api_key: str, payload: dict):
        headers = {
            "x-api-key": api_key,
            "x-output-format": "mp3",
            "Content-Type": "application/json",
        }

        response = None
        try:
            response = self._requests_post(
                _VOAI_TTS_URL,
                headers=headers,
                json=payload,
                timeout=(5, 45),
            )
            response.raise_for_status()

            content_type = str(response.headers.get("content-type", "") or "").lower()
            if "audio" not in content_type and "octet-stream" not in content_type:
                self.finished_signal.emit(
                    False,
                    f"VoAI 回傳非音訊格式：{content_type}",
                    self._build_result_payload(format="mp3"),
                )
                return

            audio_bytes = response.content
            if not audio_bytes:
                self.finished_signal.emit(False, "VoAI 回傳空音訊。", self._build_result_payload(format="mp3"))
                return

            self.progress_signal.emit(
                "stream_started",
                {
                    "reply_id": self._reply_id,
                    "trace_id": self._trace_id,
                    "bytes_received": len(audio_bytes),
                    "format": "mp3",
                },
            )

            audio_buffer = io.BytesIO(audio_bytes)
            audio_buffer.seek(0)
            self.audio_ready_signal.emit(audio_buffer, self._reply_id, self._trace_id)

            self.finished_signal.emit(
                True,
                "VoAI TTS 音訊取得完成，已送入播放佇列。",
                {
                    "reply_id": self._reply_id,
                    "trace_id": self._trace_id,
                    "text": self._text,
                    "bytes_received": len(audio_bytes),
                    "format": "mp3",
                    "selected_provider": "voai",
                    "queued_playback": True,
                },
            )
        except requests.HTTPError as exc:
            body = ""
            try:
                body = exc.response.text[:200] if exc.response is not None else ""
            except Exception:
                pass
            reason_code, detail, definitive = _classify_fast_fail(exc)
            if definitive and self._adaptive_fallback_enabled:
                self.finished_signal.emit(
                    False,
                    f"VoAI API 請求失敗 ({exc}): {body}",
                    self._build_fast_fail_payload(
                        stage="mp3",
                        reason_code=reason_code,
                        detail=detail,
                        audio_format="mp3",
                    ),
                )
                return
            self.finished_signal.emit(
                False,
                f"VoAI API 請求失敗 ({exc}): {body}",
                self._build_result_payload(format="mp3"),
            )
        except requests.RequestException as exc:
            reason_code, detail, definitive = _classify_fast_fail(exc)
            if definitive and self._adaptive_fallback_enabled:
                self.finished_signal.emit(
                    False,
                    f"VoAI 網路錯誤: {exc}",
                    self._build_fast_fail_payload(
                        stage="mp3",
                        reason_code=reason_code,
                        detail=detail,
                        audio_format="mp3",
                    ),
                )
                return
            self.finished_signal.emit(
                False,
                f"VoAI 網路錯誤: {exc}",
                self._build_result_payload(format="mp3"),
            )
        except Exception as exc:
            self.finished_signal.emit(
                False,
                f"VoAI TTS 取得失敗: {exc}",
                self._build_result_payload(format="mp3"),
            )
        finally:
            if response is not None:
                response.close()
