"""
ECHOES — Python 端 ElevenLabs 串流 TTS。

將句讀級文字片段送往 ElevenLabs 串流 API，將回傳音訊累積在記憶體中，
透過 audio_ready_signal 通知 AudioStreamWorker 播放，實現 Producer-Consumer 無縫銜接。
"""

from __future__ import annotations

import io
import os
from uuid import uuid4

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config
from audio_playback import PygameInMemoryAudioPlayer

def _sanitize_stream_tts_text(text: str) -> str:
    return str(text or "").strip()


class ElevenLabsStreamingTTSWorker(QThread):
    """以串流方式取得 ElevenLabs 音訊位元組，不負責播放。

    音訊取得完畢後 emit audio_ready_signal，由 AudioStreamWorker 負責排隊播放。
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
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._reply_id = (reply_id or uuid4().hex).strip()
        self._trace_id = (trace_id or "").strip()
        self._voice_id = (voice_id or "").strip()
        self._requests_post = requests_post or requests.post

    def run(self):
        speech_text = _sanitize_stream_tts_text(self._text)
        if not speech_text:
            self.finished_signal.emit(False, "略過串流 TTS：沒有可朗讀的文字。", None)
            return

        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        voice_id = self._voice_id or config.ELEVENLABS_VOICE_ID
        if not api_key or not voice_id:
            self.finished_signal.emit(False, "略過串流 TTS：缺少 ElevenLabs API Key 或 Voice ID。", None)
            return

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        }
        payload = {
            "text": speech_text,
            "model_id": os.getenv("ELEVENLABS_MODEL_ID", config.DEFAULT_TTS_MODEL_ID).strip()
            or config.DEFAULT_TTS_MODEL_ID,
            "voice_settings": {
                "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.45")),
                "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75")),
                "use_speaker_boost": os.getenv("ELEVENLABS_USE_SPEAKER_BOOST", "false").strip().lower()
                not in {"0", "false", "no", "off"},
                "style": float(os.getenv("ELEVENLABS_STYLE", "0.0")),
                "speed": float(os.getenv("ELEVENLABS_SPEED", "1.15")),
            },
        }

        response = None
        bytes_forwarded = 0
        audio_buffer = io.BytesIO()
        try:
            response = self._requests_post(
                url,
                headers=headers,
                params={
                    "output_format": os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_22050_32"),
                    "optimize_streaming_latency": os.getenv("ELEVENLABS_OPTIMIZE_STREAMING_LATENCY", "3"),
                },
                json=payload,
                timeout=config.DEFAULT_TTS_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            content_type = str(response.headers.get("content-type", "") or "").lower()
            if "audio" not in content_type:
                self.finished_signal.emit(False, "ElevenLabs 串流回傳了無效音訊格式。", None)
                return

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
                        },
                    )
                bytes_forwarded += len(chunk)
                audio_buffer.write(chunk)

            if bytes_forwarded <= 0:
                self.finished_signal.emit(False, "ElevenLabs 串流未收到可播放音訊資料。", None)
                return

            audio_buffer.seek(0)
            self.audio_ready_signal.emit(audio_buffer, self._reply_id, self._trace_id)

            result_payload = {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "text": speech_text,
                "bytes_forwarded": bytes_forwarded,
            }
            self.finished_signal.emit(True, "TTS 音訊取得完成，已送入播放佇列。", result_payload)
        except requests.RequestException as exc:
            self.finished_signal.emit(False, f"ElevenLabs 串流請求失敗: {exc}", None)
        except Exception as exc:  # pragma: no cover - 依外部音訊環境而定
            self.finished_signal.emit(False, f"TTS 音訊取得失敗: {exc}", None)
        finally:
            if response is not None:
                response.close()
