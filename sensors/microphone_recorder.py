"""MicrophoneRecorder — 麥克風收音的裝置層：只管 audio device、stream、PCM buffer
與錄音生命週期。不載入模型、不呼叫 Harness。

單次 recording session 對應單一 audio buffer；mono / 16kHz / float32；
超過 max_recording_seconds 自動停止收音，防止無限記憶體成長。
"""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd


class MicrophoneError(Exception):
    """麥克風開啟或收音時的錯誤（裝置不存在、權限不足、裝置中途失效）。"""


class MicrophoneRecorder:
    def __init__(self, sample_rate: int, max_recording_seconds: float) -> None:
        self._sample_rate = int(sample_rate)
        self._max_samples = max(1, int(sample_rate * max_recording_seconds))
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._total_samples = 0
        self._lock = threading.Lock()
        self._active = False
        self.max_reached = threading.Event()
        self.device_failed = threading.Event()

    def start(self) -> None:
        if self._active:
            return
        self._chunks = []
        self._total_samples = 0
        self.max_reached.clear()
        self.device_failed.clear()
        try:
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
            stream.start()
        except Exception as exc:  # noqa: BLE001
            raise MicrophoneError(str(exc)) from exc
        self._stream = stream
        self._active = True
        print(f"[MicrophoneRecorder] 已開啟輸入裝置：{self._describe_default_input_device()}")

    @staticmethod
    def _describe_default_input_device() -> str:
        # ponytail: 純診斷用途，查詢裝置名稱失敗不應影響錄音本身。
        try:
            device_index = sd.default.device[0]
            info = sd.query_devices(device_index)
            return f"[{device_index}] {info['name']}"
        except Exception as exc:  # noqa: BLE001
            return f"unknown ({exc})"

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            # ponytail: v1 對任何非零 status flag 一律視為裝置異常並結束收音,
            # 不細分 overflow/disconnect;若日常使用發現誤判過多再細分。
            with self._lock:
                self._active = False
            print(f"[MicrophoneRecorder] 裝置狀態異常，停止收音：{status}")
            self.device_failed.set()
            raise sd.CallbackStop()
        with self._lock:
            if self._total_samples >= self._max_samples:
                return
            chunk = np.asarray(indata[:, 0], dtype=np.float32)
            remaining = self._max_samples - self._total_samples
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            chunk = chunk.copy()
            self._chunks.append(chunk)
            self._total_samples += len(chunk)
            reached_max = self._total_samples >= self._max_samples
        if reached_max:
            print("[MicrophoneRecorder] 已達最大錄音長度，自動停止收音")
            self.max_reached.set()
            raise sd.CallbackStop()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._active = False

    def get_audio(self) -> np.ndarray:
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)

    def read_new_chunks(self, cursor: int) -> tuple[np.ndarray, int]:
        """Return chunks added since ``cursor`` and the cursor for the next read."""
        with self._lock:
            start = min(max(int(cursor), 0), len(self._chunks))
            new_chunks = self._chunks[start:]
            if not new_chunks:
                return np.zeros(0, dtype=np.float32), len(self._chunks)
            return np.concatenate(new_chunks), len(self._chunks)

    def shutdown(self) -> None:
        self.stop()

    @property
    def is_active(self) -> bool:
        return self._active
