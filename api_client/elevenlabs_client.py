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
from audio_playback import is_raw_pcm_content_type

# 沿用 voai_client.py 已經在用的模式:共用一個 Session 讓 TCP/TLS 連線可以重用,
# 不必每個句段都重新握手(實測單次 TCP+TLS 建立約 0.4-1.4 秒)。原本這裡用的是
# requests.post 模組函式,每次呼叫都會開一個新的 Session、新的連線。
_ELEVENLABS_HTTP_SESSION = requests.Session()


def _sanitize_stream_tts_text(text: str) -> str:
    return str(text or "").strip()


# 各 ElevenLabs 模型的請求限制；未列出的鍵視為無限制，沿用全域環境變數原值。
# - eleven_v3 拒絕 optimize_streaming_latency(400 unsupported_model),之前所有角色
#   都因此 400、全部 fallback 到 VoAI 的共用預設聲線,才會聽起來像同一人。
# - eleven_flash_v2_5 的 speed 合法範圍是 0.7-1.2,超出會 400 invalid_voice_settings。
# 兩點皆由 openspec/changes/per-character-tts-model/design.md 的 smoke 測試實測確認。
MODEL_CAPABILITIES: dict[str, dict] = {
    "eleven_v3": {"supports_optimize_streaming_latency": False},
    "eleven_flash_v2_5": {"speed_range": (0.7, 1.2)},
}


def _model_capabilities(model_id: str) -> dict:
    return MODEL_CAPABILITIES.get(model_id, {})


def _clamp_to_model_range(model_id: str, setting_name: str, value: float) -> float:
    """把某個 voice_settings 值夾進所選模型的合法範圍；沒有範圍限制就原值放行。"""
    value_range = _model_capabilities(model_id).get(f"{setting_name}_range")
    if value_range is None:
        return value
    low, high = value_range
    return max(low, min(high, value))


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
        model_id: str | None = None,
        pcm_stream_sink=None,
        requests_post=None,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._reply_id = (reply_id or uuid4().hex).strip()
        self._trace_id = (trace_id or "").strip()
        self._voice_id = (voice_id or "").strip()
        self._model_id = (model_id or "").strip()
        self._pcm_stream_sink = pcm_stream_sink
        self._requests_post = requests_post or _ELEVENLABS_HTTP_SESSION.post

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
            "Accept": "audio/pcm" if self._pcm_stream_sink is not None else "audio/mpeg",
            "Content-Type": "application/json",
        }
        model_id = self._model_id or (
            os.getenv("ELEVENLABS_MODEL_ID", config.DEFAULT_TTS_MODEL_ID).strip()
            or config.DEFAULT_TTS_MODEL_ID
        )
        payload = {
            "text": speech_text,
            "model_id": model_id,
            "voice_settings": {
                "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.45")),
                "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75")),
                "use_speaker_boost": os.getenv("ELEVENLABS_USE_SPEAKER_BOOST", "false").strip().lower()
                not in {"0", "false", "no", "off"},
                "style": float(os.getenv("ELEVENLABS_STYLE", "0.0")),
                "speed": _clamp_to_model_range(
                    model_id, "speed", float(os.getenv("ELEVENLABS_SPEED", "1.15"))
                ),
            },
        }

        response = None
        bytes_forwarded = 0
        audio_buffer = io.BytesIO() if self._pcm_stream_sink is None else None
        pcm_segment_started = False
        pcm_segment_finished = False
        try:
            params = {
                "output_format": f"pcm_{config.TTS_PCM_SAMPLE_RATE}" if self._pcm_stream_sink is not None else os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_22050_32"),
            }
            if _model_capabilities(model_id).get("supports_optimize_streaming_latency", True):
                params["optimize_streaming_latency"] = os.getenv("ELEVENLABS_OPTIMIZE_STREAMING_LATENCY", "3")
            response = self._requests_post(
                url,
                headers=headers,
                params=params,
                json=payload,
                timeout=config.DEFAULT_TTS_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            content_type = str(response.headers.get("content-type", "") or "").lower()
            if "audio" not in content_type:
                self.finished_signal.emit(False, "ElevenLabs 串流回傳了無效音訊格式。", None)
                return
            # 要的是 pcm_{rate} 就必須拿到 raw PCM。舊的檢查只看有沒有 "audio",
            # audio/mpeg 一樣通過,MP3 位元組就這樣被當成 s16le 播成雜訊。
            if self._pcm_stream_sink is not None and not is_raw_pcm_content_type(content_type):
                self.finished_signal.emit(False, f"ElevenLabs 要求 PCM 但回傳 {content_type}。", None)
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
                if self._pcm_stream_sink is not None:
                    self._pcm_stream_sink.enqueue_pcm_chunk(
                        chunk,
                        self._reply_id,
                        self._trace_id,
                        sample_rate=config.TTS_PCM_SAMPLE_RATE,
                    )
                    pcm_segment_started = True
                else:
                    audio_buffer.write(chunk)

            if bytes_forwarded <= 0:
                self.finished_signal.emit(False, "ElevenLabs 串流未收到可播放音訊資料。", None)
                return

            if self._pcm_stream_sink is not None:
                self._pcm_stream_sink.finish_pcm_segment(self._reply_id, self._trace_id)
                pcm_segment_finished = True
            else:
                audio_buffer.seek(0)
                self.audio_ready_signal.emit(audio_buffer, self._reply_id, self._trace_id)

            result_payload = {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "text": speech_text,
                "bytes_forwarded": bytes_forwarded,
                "queued_playback": True,
                "format": "pcm" if self._pcm_stream_sink is not None else "mp3",
            }
            self.finished_signal.emit(True, "TTS 音訊取得完成，已送入播放佇列。", result_payload)
        except requests.RequestException as exc:
            self.finished_signal.emit(False, f"ElevenLabs 串流請求失敗: {exc}", None)
        except Exception as exc:  # pragma: no cover - 依外部音訊環境而定
            self.finished_signal.emit(False, f"TTS 音訊取得失敗: {exc}", None)
        finally:
            if self._pcm_stream_sink is not None and pcm_segment_started and not pcm_segment_finished:
                self._pcm_stream_sink.finish_pcm_segment(self._reply_id, self._trace_id)
            if response is not None:
                response.close()
