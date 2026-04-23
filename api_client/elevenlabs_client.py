"""
ECHOES — Python 端 ElevenLabs 串流 TTS 與背景音訊播放。

將句讀級文字片段送往 ElevenLabs 串流 API，並把回傳音訊直接 pipe 給
本地 `ffplay` 背景播放器，避免先寫入暫存 MP3 再交由前端播放。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from uuid import uuid4

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config


def _sanitize_stream_tts_text(text: str) -> str:
    return str(text or "").strip()


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
        popen_factory=None,
        which_resolver=None,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._reply_id = (reply_id or uuid4().hex).strip()
        self._trace_id = (trace_id or "").strip()
        self._voice_id = (voice_id or "").strip()
        self._requests_post = requests_post or requests.post
        self._popen_factory = popen_factory or subprocess.Popen
        self._which_resolver = which_resolver or shutil.which

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

        ffplay_binary = self._which_resolver("ffplay")
        if not ffplay_binary:
            self.finished_signal.emit(False, "略過串流 TTS：系統找不到 ffplay。", None)
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
                "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.8")),
            },
        }

        player = None
        response = None
        bytes_forwarded = 0
        try:
            response = self._requests_post(
                url,
                headers=headers,
                json=payload,
                timeout=config.DEFAULT_TTS_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            content_type = str(response.headers.get("content-type", "") or "").lower()
            if "audio" not in content_type:
                self.finished_signal.emit(False, "ElevenLabs 串流回傳了無效音訊格式。", None)
                return

            player = self._popen_factory(
                [
                    ffplay_binary,
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "error",
                    "-i",
                    "pipe:0",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            if player.stdin is None:
                self.finished_signal.emit(False, "串流播放器無法建立 stdin pipe。", None)
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
                player.stdin.write(chunk)
                player.stdin.flush()

            if bytes_forwarded <= 0:
                self.finished_signal.emit(False, "ElevenLabs 串流未收到可播放音訊資料。", None)
                return

            player.stdin.close()
            player.stdin = None
            return_code = player.wait(timeout=120)
            if return_code != 0:
                stderr_text = ""
                if getattr(player, "stderr", None) is not None:
                    stderr_text = (player.stderr.read() or b"").decode("utf-8", errors="ignore").strip()
                detail = stderr_text or f"ffplay 結束代碼 {return_code}"
                self.finished_signal.emit(False, f"串流播放器播放失敗：{detail}", None)
                return

            payload = {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "text": speech_text,
                "bytes_forwarded": bytes_forwarded,
            }
            self.finished_signal.emit(True, "串流語音播放完成。", payload)
        except requests.RequestException as exc:
            self.finished_signal.emit(False, f"ElevenLabs 串流請求失敗: {exc}", None)
        except Exception as exc:  # pragma: no cover - 依外部播放器與系統環境而定
            self.finished_signal.emit(False, f"串流語音播放失敗: {exc}", None)
        finally:
            if response is not None:
                response.close()
            if player is not None:
                try:
                    if getattr(player, "stdin", None) is not None:
                        player.stdin.close()
                except OSError:
                    pass
                if getattr(player, "poll", lambda: None)() is None:
                    try:
                        player.kill()
                    except OSError:
                        pass
