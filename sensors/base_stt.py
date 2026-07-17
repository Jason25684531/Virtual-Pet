"""BaseSTT — 語音辨識 provider 的抽象介面。

concrete provider（例如 `FasterWhisperSTT`）只負責模型生命週期與音訊轉文字，
不得操作 UI、不得直接呼叫 controller 或 Harness。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


class SttError(Exception):
    """STT provider 相關錯誤的基底類別。"""


class SttModelLoadError(SttError):
    """模型載入失敗（例如 CUDA/cuDNN 不可用、下載失敗）。"""


class SttTranscriptionError(SttError):
    """推論過程失敗。"""


@dataclass(frozen=True)
class TranscriptionResult:
    """單次 transcribe() 的結果，供 controller 判斷有效性並記錄 debug metadata。"""

    text: str
    language: str
    language_probability: float
    audio_duration_seconds: float
    inference_duration_seconds: float


class BaseSTT(ABC):
    """語音辨識 provider ABC；callers 只依賴此介面，不依賴 concrete class。"""

    @abstractmethod
    def setup(self) -> None:
        """載入模型。冪等，可在背景 thread 呼叫；失敗時拋 SttModelLoadError。"""

    @abstractmethod
    def is_ready(self) -> bool:
        """模型是否已成功載入且可用。"""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscriptionResult:
        """將完整錄音 buffer 轉為文字。失敗時拋 SttTranscriptionError。"""

    @abstractmethod
    def shutdown(self) -> None:
        """釋放模型資源。冪等。"""

    @property
    @abstractmethod
    def last_error(self) -> str:
        """最近一次錯誤的一句話摘要（供 VoiceRuntimeStatusAdapter 讀取）。"""
