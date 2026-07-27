"""FasterWhisperSTT — faster-whisper 的 BaseSTT 實作。

faster_whisper 的 import 與 API 只存在於這一個檔案，避免第三方 library 散落在
UI 或 Harness。CUDA-only（第一版無 CPU fallback），模型全程只載入一次。
"""

from __future__ import annotations

import importlib
import os
import threading
import time

import numpy as np

from sensors.base_stt import BaseSTT, SttModelLoadError, SttTranscriptionError, TranscriptionResult


def _register_windows_cuda_dll_directories() -> None:
    """pip 安裝的 nvidia-cublas-cu12/nvidia-cudnn-cu12/nvidia-cuda-nvrtc-cu12 wheel
    不會自動讓 ctranslate2 找到 cublas64_12.dll。ctranslate2 的原生 DLL loader 走的是
    傳統 PATH 搜尋（實測 os.add_dll_directory() 對它無效，須直接改 PATH），
    因此把各 wheel 的 bin/ 目錄前置進 PATH。best-effort：找不到套件（例如改用
    系統層 CUDA Toolkit）就靜默略過，不影響 setup() 既有的失敗處理。"""
    if os.name != "nt":
        return
    bin_dirs = []
    for package_name in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            package = importlib.import_module(package_name)
            bin_dir = os.path.join(next(iter(package.__path__)), "bin")
            if os.path.isdir(bin_dir):
                bin_dirs.append(bin_dir)
        except Exception:  # noqa: BLE001
            pass
    if bin_dirs:
        os.environ["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + os.environ.get("PATH", "")


class FasterWhisperSTT(BaseSTT):
    def __init__(
        self,
        model_name: str,
        device: str,
        compute_type: str,
        download_root: str,
        language: str | None = None,
        beam_size: int = 1,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._download_root = download_root
        self._language = language or None  # 空字串/None -> auto detection
        self._beam_size = beam_size
        self._model = None
        self._lock = threading.Lock()
        self._last_error = ""

    def setup(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                _register_windows_cuda_dll_directories()
                from faster_whisper import WhisperModel #匯入模型

                self._model = WhisperModel(
                    self._model_name,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=self._download_root,
                )
                self._last_error = ""
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise SttModelLoadError(str(exc)) from exc

    def is_ready(self) -> bool:
        with self._lock:
            return self._model is not None

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscriptionResult:
        model = self._model
        if model is None:
            raise SttTranscriptionError("model not loaded")
        started_at = time.monotonic()
        try:
            segments, info = model.transcribe(
                audio,
                language=self._language,
                task="transcribe",
                beam_size=self._beam_size,
            )
            # segments 為 lazy generator;完整消費才能確保推論完成並取得完整文字。
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            raise SttTranscriptionError(str(exc)) from exc
        inference_duration_seconds = time.monotonic() - started_at
        return TranscriptionResult(
            text=text,
            language=str(info.language or ""),
            language_probability=float(info.language_probability or 0.0),
            audio_duration_seconds=float(info.duration or 0.0),
            inference_duration_seconds=inference_duration_seconds,
        )

    def shutdown(self) -> None:
        with self._lock:
            self._model = None

    @property
    def last_error(self) -> str:
        return self._last_error
