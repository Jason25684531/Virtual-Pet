"""
Provider-neutral audio playback helpers for ECHOES.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable

try:
    import pygame
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    pygame = None  # type: ignore[assignment]


class PlaybackStartSuppressed(RuntimeError):
    """Raised when a queued reply is intentionally suppressed before playback starts."""


class PygameInMemoryAudioPlayer:
    """Play a complete MP3 buffer from memory through pygame."""

    _global_lock = threading.Lock()

    def __init__(self, mixer_module=None, poll_interval: float = 0.02):
        self._mixer = mixer_module or (pygame.mixer if pygame is not None else None)
        self._poll_interval = poll_interval
        self._initialized = False

    def play(self, audio_buffer: io.BytesIO, before_start=None):
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

            self._mixer.music.load(audio_buffer, "mp3")
            if callable(before_start) and before_start() is False:
                raise PlaybackStartSuppressed("記憶體音訊在起播前被抑制。")
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
            buffer=int(os.getenv("PYGAME_MIXER_BUFFER", "512")),
        )
        self._initialized = True


class FfplayPcmAudioPlayer:
    """Stream signed 16-bit little-endian PCM chunks into ffplay stdin."""

    def __init__(
        self,
        ffplay_path: str | None = None,
        sample_rate: int = 32000,
        channels: int = 1,
        popen_factory=None,
    ):
        self._ffplay_path = ffplay_path or shutil.which("ffplay")
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
                "-ac",
                str(self._channels),
                "-i",
                "pipe:0",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
                    pass
            process.wait()
        return bytes_written
