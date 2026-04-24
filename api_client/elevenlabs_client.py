"""
ECHOES — Python 端 ElevenLabs 串流 TTS 與記憶體音訊播放。

將句讀級文字片段送往 ElevenLabs 串流 API，將回傳音訊累積在記憶體中，
再交給 `pygame` 背景播放器直接播放，避免暫存 MP3 與硬碟 I/O。
"""

from __future__ import annotations

import io
import os
import threading
import time
from uuid import uuid4

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config

try:
    import pygame
except ModuleNotFoundError:  # pragma: no cover - 允許依賴未安裝時安全降級
    pygame = None  # type: ignore[assignment]


def _sanitize_stream_tts_text(text: str) -> str:
    return str(text or "").strip()


class PygameInMemoryAudioPlayer:
    """以 `pygame.mixer.music` 從記憶體播放單段 MP3 音訊。"""

    _global_lock = threading.Lock()

    def __init__(self, mixer_module=None, poll_interval: float = 0.02):
        self._mixer = mixer_module or (pygame.mixer if pygame is not None else None)
        self._poll_interval = poll_interval
        self._initialized = False

    def play(self, audio_buffer: io.BytesIO):
        if self._mixer is None:
            raise RuntimeError("pygame 尚未安裝，無法播放記憶體音訊。")

        with self._global_lock:
            self._ensure_initialized()
            audio_buffer.seek(0)
            try:
                self._mixer.music.stop()
            except Exception:
                pass
            try:
                self._mixer.music.unload()
            except Exception:
                pass

            # `namehint=\"mp3\"` 可幫助 pygame 在 file-like object 上正確判斷格式。
            self._mixer.music.load(audio_buffer, "mp3")
            self._mixer.music.play()
            while self._mixer.music.get_busy():
                time.sleep(self._poll_interval)

    def _ensure_initialized(self):
        get_init = getattr(self._mixer, "get_init", None)
        if callable(get_init) and get_init():
            self._initialized = True
            return

        init = getattr(self._mixer, "init", None)
        if not callable(init):
            raise RuntimeError("pygame mixer 無法初始化。")

        init(
            frequency=int(os.getenv("PYGAME_MIXER_FREQUENCY", "22050")),
            size=int(os.getenv("PYGAME_MIXER_SIZE", "-16")),
            channels=int(os.getenv("PYGAME_MIXER_CHANNELS", "2")),
            buffer=int(os.getenv("PYGAME_MIXER_BUFFER", "4096")),
        )
        self._initialized = True


class ElevenLabsStreamingTTSWorker(QThread):
    """以串流方式取得 ElevenLabs 音訊，並由 Python 背景直接播放。"""

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        requests_post=None,
        audio_player=None,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._reply_id = (reply_id or uuid4().hex).strip()
        self._trace_id = (trace_id or "").strip()
        self._voice_id = (voice_id or "").strip()
        self._requests_post = requests_post or requests.post
        self._audio_player = audio_player or PygameInMemoryAudioPlayer()

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
            self._audio_player.play(audio_buffer)

            payload = {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "text": speech_text,
                "bytes_forwarded": bytes_forwarded,
            }
            self.finished_signal.emit(True, "串流語音播放完成。", payload)
        except requests.RequestException as exc:
            self.finished_signal.emit(False, f"ElevenLabs 串流請求失敗: {exc}", None)
        except Exception as exc:  # pragma: no cover - 依外部音訊環境而定
            self.finished_signal.emit(False, f"串流語音播放失敗: {exc}", None)
        finally:
            if response is not None:
                response.close()
