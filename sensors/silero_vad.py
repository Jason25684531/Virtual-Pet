"""Silero VAD speech-endpoint detection for push-to-talk recording sessions."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np

LOGGER = logging.getLogger(__name__)


MODEL_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/"
    "src/silero_vad/data/silero_vad.onnx"
)
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


class SileroVad:
    """Detect a speech endpoint from consecutive 16 kHz mono PCM samples."""

    SAMPLE_RATE = 16_000
    FRAME_SAMPLES = 512
    CONTEXT_SAMPLES = 64
    FRAME_DURATION_MS = 32
    SPEECH_START_MS = 250

    def __init__(
        self,
        *,
        silence_ms: int = 500,
        threshold: float = 0.5,
        cache_dir: Path | None = None,
        downloader: Callable[[str, Path], object] | None = None,
        session_factory: Callable[[Path], object] | None = None,
        model_url: str = MODEL_URL,
        model_sha256: str = MODEL_SHA256,
    ) -> None:
        self._silence_ms = int(silence_ms)
        self._threshold = float(threshold)
        self._negative_threshold = self._threshold - 0.15
        self._cache_dir = cache_dir or Path(__file__).resolve().parent.parent / "runtime_cache" / "vad"
        self._downloader = downloader or self._download
        self._session_factory = session_factory
        self._model_url = model_url
        self._model_sha256 = model_sha256.lower()
        self._lock = threading.RLock()
        self._session: object | None = None
        self._ready = False
        self._failure_logged = False
        self._failure_reason = ""
        self.reset()

    def setup(self) -> bool:
        """Prepare the model without allowing download or runtime failures to escape."""
        with self._lock:
            if self._ready:
                return True
            try:
                model_path = self._ensure_model()
                self._session = self._create_session(model_path)
                self._ready = True
                self.reset()
                LOGGER.info("[VAD] model ready (cache=%s)", model_path)
                return True
            except Exception as exc:  # noqa: BLE001 - VAD must fail open
                self._disable(exc)
                return False

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def feed_audio(self, samples: np.ndarray) -> bool:
        """Consume new PCM samples and return True exactly once at Speech Endpoint."""
        with self._lock:
            if not self._ready or self._endpoint_reported:
                return False
            try:
                incoming = np.asarray(samples, dtype=np.float32).reshape(-1)
                if incoming.size:
                    self._pending = np.concatenate((self._pending, incoming))
                while self._pending.size >= self.FRAME_SAMPLES:
                    frame, self._pending = (
                        self._pending[: self.FRAME_SAMPLES],
                        self._pending[self.FRAME_SAMPLES :],
                    )
                    if self._advance(float(self._infer(frame))):
                        self._endpoint_reported = True
                        return True
            except Exception as exc:  # noqa: BLE001 - preserve manual-stop STT
                self._disable(exc)
            return False

    def reset(self) -> None:
        """Clear endpoint and recurrent model state for a new Recording Session."""
        with self._lock:
            self._pending = np.empty(0, dtype=np.float32)
            self._context = np.zeros(self.CONTEXT_SAMPLES, dtype=np.float32)
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
            self._speech_frames = 0
            self._silence_frames = 0
            self._speech_started = False
            self._endpoint_reported = False

    def shutdown(self) -> None:
        """Release the ONNX session; repeat calls are safe."""
        with self._lock:
            self._session = None
            self._ready = False
            self.reset()

    def _advance(self, probability: float) -> bool:
        if not self._speech_started:
            if probability >= self._threshold:
                self._speech_frames += 1
                if self._speech_frames * self.FRAME_DURATION_MS >= self.SPEECH_START_MS:
                    self._speech_started = True
                    self._silence_frames = 0
                    LOGGER.info("[VAD] Speech Start detected (probability=%.2f)", probability)
            else:
                self._speech_frames = 0
            return False

        if probability < self._negative_threshold:
            self._silence_frames += 1
            endpoint_detected = self._silence_frames * self.FRAME_DURATION_MS >= self._silence_ms
            if endpoint_detected:
                LOGGER.info("[VAD] Speech Endpoint detected (silence_ms=%s)", self._silence_ms)
            return endpoint_detected

        self._silence_frames = 0
        return False

    def _infer(self, frame: np.ndarray) -> float:
        if self._session is None:
            raise RuntimeError("VAD session is unavailable")
        # Silero v5 每個 512-sample frame 前必須附上前 64 samples 的 context。
        outputs = self._session.run(  # type: ignore[union-attr]
            None,
            {
                "input": np.concatenate((self._context, frame)).reshape(1, -1),
                "state": self._state,
                "sr": np.array(self.SAMPLE_RATE, dtype=np.int64),
            },
        )
        self._context = frame[-self.CONTEXT_SAMPLES :].copy()
        self._state = np.asarray(outputs[1], dtype=np.float32)
        return float(np.asarray(outputs[0]).reshape(-1)[0])

    def _ensure_model(self) -> Path:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        model_path = self._cache_dir / "silero_vad.onnx"
        if model_path.is_file() and self._has_expected_hash(model_path):
            return model_path
        if model_path.exists():
            model_path.unlink()
        partial_path = model_path.with_suffix(".onnx.part")
        partial_path.unlink(missing_ok=True)
        try:
            self._downloader(self._model_url, partial_path)
            if not self._has_expected_hash(partial_path):
                raise RuntimeError("downloaded Silero VAD model checksum did not match")
            partial_path.replace(model_path)
            return model_path
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _download(url: str, destination: Path) -> None:
        urlretrieve(url, destination)  # noqa: S310 - fixed, checksum-verified model URL

    def _create_session(self, model_path: Path) -> object:
        if self._session_factory is not None:
            return self._session_factory(model_path)
        import onnxruntime

        return onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )

    def _has_expected_hash(self, model_path: Path) -> bool:
        digest = hashlib.sha256()
        with model_path.open("rb") as model_file:
            for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == self._model_sha256

    def _disable(self, exc: Exception) -> None:
        self._ready = False
        self._session = None
        self._failure_reason = str(exc)
        if not self._failure_logged:
            LOGGER.warning("[VAD] disabled; falling back to manual stop: %s", exc)
            self._failure_logged = True
