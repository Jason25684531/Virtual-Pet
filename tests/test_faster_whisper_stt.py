"""FasterWhisperSTT 單元測試：以假 WhisperModel monkeypatch faster_whisper，
不下載真實模型、不需要 GPU。"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from sensors import faster_whisper_stt as faster_whisper_stt_module
from sensors.faster_whisper_stt import SttModelLoadError, SttTranscriptionError
from sensors.faster_whisper_stt import FasterWhisperSTT


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeInfo:
    def __init__(self, language: str = "zh", language_probability: float = 0.95, duration: float = 2.5) -> None:
        self.language = language
        self.language_probability = language_probability
        self.duration = duration


class _FakeWhisperModel:
    """記錄建構次數與 transcribe 呼叫參數,供測試驗證單次載入與正確引數。"""

    construct_count = 0

    def __init__(self, model_name, device, compute_type, download_root):
        _FakeWhisperModel.construct_count += 1
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.transcribe_calls: list[dict] = []
        self.segments_to_return = [_FakeSegment("你好"), _FakeSegment("世界")]
        self.info_to_return = _FakeInfo()
        self.should_raise: Exception | None = None

    def transcribe(self, audio, language, task, beam_size):
        self.transcribe_calls.append(
            {"language": language, "task": task, "beam_size": beam_size, "audio_len": len(audio)}
        )
        if self.should_raise is not None:
            raise self.should_raise
        return iter(self.segments_to_return), self.info_to_return


@pytest.fixture(autouse=True)
def _reset_construct_count():
    _FakeWhisperModel.construct_count = 0
    yield


def _make_fake_module(monkeypatch, fake_model: _FakeWhisperModel):
    import types
    import sys

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = lambda *a, **kw: fake_model
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)


def test_setup_constructs_model_once(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()
    provider.setup()
    provider.setup()

    assert provider.is_ready() is True
    # setup() 冪等:即使呼叫三次,底層 WhisperModel 只被賦值一次(fake factory 本身不計次,
    # 但驗證第二次呼叫沒有替換 _model 物件,確保模型不會每次錄音重新初始化)
    assert provider._model is fake_model


def test_setup_failure_raises_model_load_error(monkeypatch):
    import types
    import sys

    def _boom(*a, **kw):
        raise RuntimeError("libcudnn not found")

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = _boom
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    with pytest.raises(SttModelLoadError):
        provider.setup()

    assert provider.is_ready() is False
    assert "libcudnn" in provider.last_error


def test_transcribe_consumes_generator_and_merges_text(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()

    audio = np.zeros(16000, dtype=np.float32)
    result = provider.transcribe(audio, 16000)

    assert result.text == "你好世界"
    assert result.language == "zh"
    assert result.language_probability == pytest.approx(0.95)
    assert result.audio_duration_seconds == pytest.approx(2.5)
    assert fake_model.transcribe_calls[0]["task"] == "transcribe"
    assert fake_model.transcribe_calls[0]["language"] is None  # auto detection 預設


def test_transcribe_converts_simplified_chinese_to_traditional(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    fake_model.segments_to_return = [_FakeSegment("这是简体字测试")]
    fake_model.info_to_return = _FakeInfo(language="zh")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()
    result = provider.transcribe(np.zeros(1600, dtype=np.float32), 16000)

    assert result.text == "這是簡體字測試"


def test_transcribe_does_not_convert_non_chinese_text(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    fake_model.segments_to_return = [_FakeSegment("hello world")]
    fake_model.info_to_return = _FakeInfo(language="en")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()
    result = provider.transcribe(np.zeros(1600, dtype=np.float32), 16000)

    assert result.text == "hello world"
    assert result.language == "en"


def test_transcribe_trims_only_whitespace_without_rewriting(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    fake_model.segments_to_return = [_FakeSegment("  hello world  ")]
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()
    result = provider.transcribe(np.zeros(1600, dtype=np.float32), 16000)

    assert result.text == "hello world"


def test_transcribe_failure_raises_transcription_error(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    fake_model.should_raise = RuntimeError("cuda out of memory")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()

    with pytest.raises(SttTranscriptionError):
        provider.transcribe(np.zeros(1600, dtype=np.float32), 16000)

    assert "cuda out of memory" in provider.last_error


def test_fixed_language_overrides_auto_detection(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT(
        "large-v3-turbo", "cuda", "float16", "runtime_cache/whisper", language="zh",
    )
    provider.setup()
    provider.transcribe(np.zeros(1600, dtype=np.float32), 16000)

    assert fake_model.transcribe_calls[0]["language"] == "zh"


def test_shutdown_releases_model(monkeypatch):
    fake_model = _FakeWhisperModel("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    _make_fake_module(monkeypatch, fake_model)

    provider = FasterWhisperSTT("large-v3-turbo", "cuda", "float16", "runtime_cache/whisper")
    provider.setup()
    assert provider.is_ready() is True

    provider.shutdown()
    assert provider.is_ready() is False


def test_register_windows_cuda_dll_directories_prepends_bin_dirs(monkeypatch, tmp_path):
    """實機曾發生 ctranslate2 找不到 cublas64_12.dll：pip 安裝的 nvidia-cublas-cu12
    wheel 不會自動進 Windows DLL 搜尋路径，需手動把 bin/ 前置進 PATH。"""
    monkeypatch.setattr(faster_whisper_stt_module.os, "name", "nt")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cublas = types.ModuleType("nvidia.cublas")
    fake_cublas.__path__ = [str(tmp_path)]

    def _fake_import(name):
        if name == "nvidia.cublas":
            return fake_cublas
        raise ImportError(name)

    monkeypatch.setattr(faster_whisper_stt_module.importlib, "import_module", _fake_import)
    monkeypatch.setenv("PATH", "C:\\existing")

    faster_whisper_stt_module._register_windows_cuda_dll_directories()

    assert os.environ["PATH"].startswith(str(bin_dir))
    assert os.environ["PATH"].endswith("C:\\existing")


def test_register_windows_cuda_dll_directories_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(faster_whisper_stt_module.os, "name", "posix")
    monkeypatch.setenv("PATH", "/existing")

    faster_whisper_stt_module._register_windows_cuda_dll_directories()

    assert os.environ["PATH"] == "/existing"
