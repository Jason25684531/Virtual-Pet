"""
Provider-neutral audio playback helpers for ECHOES.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable

LOGGER = logging.getLogger(__name__)

try:
    import pygame
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    pygame = None  # type: ignore[assignment]


class PlaybackStartSuppressed(RuntimeError):
    """Raised when a queued reply is intentionally suppressed before playback starts."""


# 送進 raw PCM 路徑的位元組必須真的是 raw PCM。把 MP3/WAV/Ogg 容器或錯誤訊息文字
# 當成 s16le 灌給 ffplay 就是滿耳雜訊 —— 容器 header 本身會被當成樣本播出來。
_CONTAINER_SIGNATURES = (
    (b"RIFF", "wav"), (b"ID3", "mp3"), (b"OggS", "ogg"), (b"fLaC", "flac"),
    (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"), (b"\xff\xf2", "mp3"), (b"\xff\xe3", "mp3"),
)
_RAW_PCM_CONTENT_TYPES = ("audio/pcm", "audio/l16", "audio/x-pcm", "audio/raw", "audio/x-raw", "application/octet-stream")
_TEXT_PROBE_BYTES = 16
_PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def detect_audio_container(head: bytes) -> str | None:
    """回傳這段位元組看起來像哪種容器/錯誤文字;看起來像 raw PCM 時回傳 None。

    只認明確的二進位 magic,以及「開頭 16 bytes 全是可列印 ASCII」的錯誤訊息 ——
    用單一 byte(例如 '{' 或 '<')判斷會誤傷合法的 PCM 樣本,而連續 16 個可列印
    位元組在真實語音裡幾乎不可能出現(靜音是 0x00,不可列印)。
    """
    sample = bytes(head or b"")
    name = next((name for signature, name in _CONTAINER_SIGNATURES if sample.startswith(signature)), None)
    if name is not None:
        return name
    probe = sample[:_TEXT_PROBE_BYTES]
    if len(probe) >= _TEXT_PROBE_BYTES and all(byte in _PRINTABLE for byte in probe):
        return "text"
    return None


def is_raw_pcm_content_type(content_type: str | None) -> bool:
    """只認明確宣告 raw PCM(或不帶型別的 octet-stream)的回應。

    舊的檢查是 `"audio" in content_type`,audio/mpeg 也會通過 —— 明明要的是 PCM,
    拿到 MP3 卻照樣送進 raw PCM 播放路徑。
    """
    value = str(content_type or "").lower().split(";")[0].strip()
    return any(value.startswith(allowed) for allowed in _RAW_PCM_CONTENT_TYPES)


class PygameInMemoryAudioPlayer:
    """Play a complete MP3 buffer from memory through pygame."""

    _global_lock = threading.Lock()

    def __init__(self, mixer_module=None, poll_interval: float = 0.02):
        self._mixer = mixer_module or (pygame.mixer if pygame is not None else None)
        self._poll_interval = poll_interval

    def play(self, audio_buffer: io.BytesIO, before_start=None):
        if self._mixer is None:
            raise RuntimeError("pygame 尚未安裝，無法播放記憶體音訊。")

        with self._global_lock:
            self._ensure_initialized()
            audio_buffer.seek(0)
            try:
                self._mixer.music.stop()
            except Exception:
                LOGGER.debug("pygame stop during cleanup failed", exc_info=True)
            try:
                self._mixer.music.unload()
            except Exception:
                LOGGER.debug("pygame unload during cleanup failed", exc_info=True)

            self._mixer.music.load(audio_buffer, "mp3")
            if callable(before_start) and before_start() is False:
                raise PlaybackStartSuppressed("記憶體音訊在起播前被抑制。")
            self._mixer.music.play()
            while self._mixer.music.get_busy():
                time.sleep(self._poll_interval)

    def stop(self) -> None:
        if self._mixer is not None:
            try:
                self._mixer.music.stop()
            except Exception:
                LOGGER.debug("pygame stop during cleanup failed", exc_info=True)

    def _ensure_initialized(self):
        get_init = getattr(self._mixer, "get_init", None)
        if callable(get_init) and get_init():
            return

        init = getattr(self._mixer, "init", None)
        if not callable(init):
            raise RuntimeError("pygame mixer 無法初始化。")

        init(
            frequency=int(os.getenv("PYGAME_MIXER_FREQUENCY", "22050")),
            size=int(os.getenv("PYGAME_MIXER_SIZE", "-16")),
            channels=int(os.getenv("PYGAME_MIXER_CHANNELS", "2")),
            buffer=int(os.getenv("PYGAME_MIXER_BUFFER", "512")),
        )


class FfplayPcmAudioPlayer:
    """Stream signed 16-bit little-endian PCM chunks into ffplay stdin."""

    # 省略 ffplay_path 代表「自動找」,明確傳 None 代表「沒有播放器」。兩者共用
    # 同一個預設值時,呼叫端無法表達後者,is_available() 永遠是系統裝了什麼說了算。
    AUTO_DISCOVER = object()

    def __init__(
        self,
        ffplay_path: str | None = AUTO_DISCOVER,
        sample_rate: int = 32000,
        channels: int = 1,
        popen_factory=None,
    ):
        self._ffplay_path = shutil.which("ffplay") if ffplay_path is self.AUTO_DISCOVER else ffplay_path
        self._sample_rate = int(sample_rate)
        self._channels = int(channels)
        self._popen_factory = popen_factory or subprocess.Popen

    def is_available(self) -> bool:
        return bool(self._ffplay_path)

    def play_chunks(self, chunks: Iterable[bytes], before_start=None) -> int:
        if not self._ffplay_path:
            raise RuntimeError("找不到 ffplay，無法播放 PCM 串流。")

        process = self._popen_factory(
            [
                self._ffplay_path,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-f",
                "s16le",
                "-ar",
                str(self._sample_rate),
                "-ch_layout",
                str(self._channels),
                "-i",
                "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        bytes_written = 0
        try:
            if process.stdin is None:
                raise RuntimeError("ffplay stdin 不可用。")
            started = False
            for chunk in chunks:
                if not chunk:
                    continue
                if not started:
                    started = True
                    if callable(before_start) and before_start() is False:
                        raise PlaybackStartSuppressed("PCM 音訊在起播前被抑制。")
                process.stdin.write(chunk)
                process.stdin.flush()
                bytes_written += len(chunk)
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except Exception:
                    LOGGER.debug("ffplay stdin close failed", exc_info=True)
            process.wait()
        return bytes_written
