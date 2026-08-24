"""STT config 預設值與 .env 覆寫測試（config.py 模組層常數，需 reload 驗證）。"""

from __future__ import annotations

import importlib

import dotenv

import config


def _reload_config():
    return importlib.reload(config)


def test_stt_defaults_when_unset(monkeypatch):
    for name in (
        "STT_ENABLED", "STT_MODEL", "STT_DEVICE", "STT_COMPUTE_TYPE",
        "STT_MODEL_PATH", "STT_LANGUAGE", "STT_BEAM_SIZE", "STT_SAMPLE_RATE",
        "STT_MIN_RECORDING_MS", "STT_MAX_RECORDING_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    reloaded = _reload_config()
    try:
        assert reloaded.STT_ENABLED is True
        assert reloaded.STT_MODEL == "large-v3-turbo"
        assert reloaded.STT_DEVICE == "cuda"
        assert reloaded.STT_COMPUTE_TYPE == "float16"
        assert reloaded.STT_MODEL_PATH.endswith(str(reloaded.PROJECT_ROOT / "runtime_cache" / "whisper"))
        assert reloaded.STT_LANGUAGE == ""
        assert reloaded.STT_BEAM_SIZE == 1
        assert reloaded.STT_SAMPLE_RATE == 16000
        assert reloaded.STT_MIN_RECORDING_MS == 300
        assert reloaded.STT_MAX_RECORDING_SECONDS == 30
    finally:
        _reload_config()


def test_stt_vad_defaults_to_disabled_with_documented_thresholds(monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)
    for name in ("STT_VAD_ENABLED", "STT_VAD_SILENCE_MS", "STT_VAD_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)
    reloaded = _reload_config()
    try:
        assert reloaded.STT_VAD_ENABLED is False
        assert reloaded.STT_VAD_SILENCE_MS == 500
        assert reloaded.STT_VAD_THRESHOLD == 0.5
    finally:
        _reload_config()


def test_stt_env_overrides(monkeypatch):
    monkeypatch.setenv("STT_ENABLED", "false")
    monkeypatch.setenv("STT_MODEL", "small")
    monkeypatch.setenv("STT_DEVICE", "cpu")
    monkeypatch.setenv("STT_LANGUAGE", "zh")
    monkeypatch.setenv("STT_BEAM_SIZE", "5")
    monkeypatch.setenv("STT_MAX_RECORDING_SECONDS", "10")
    reloaded = _reload_config()
    try:
        assert reloaded.STT_ENABLED is False
        assert reloaded.STT_MODEL == "small"
        assert reloaded.STT_DEVICE == "cpu"
        assert reloaded.STT_LANGUAGE == "zh"
        assert reloaded.STT_BEAM_SIZE == 5
        assert reloaded.STT_MAX_RECORDING_SECONDS == 10
    finally:
        _reload_config()


def test_stt_vad_env_overrides(monkeypatch):
    monkeypatch.setenv("STT_VAD_ENABLED", "true")
    monkeypatch.setenv("STT_VAD_SILENCE_MS", "1200")
    monkeypatch.setenv("STT_VAD_THRESHOLD", "0.65")
    reloaded = _reload_config()
    try:
        assert reloaded.STT_VAD_ENABLED is True
        assert reloaded.STT_VAD_SILENCE_MS == 1200
        assert reloaded.STT_VAD_THRESHOLD == 0.65
    finally:
        _reload_config()


def test_stt_invalid_int_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("STT_BEAM_SIZE", "not-a-number")
    reloaded = _reload_config()
    try:
        assert reloaded.STT_BEAM_SIZE == 1
    finally:
        _reload_config()


def test_stt_vad_invalid_numeric_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("STT_VAD_SILENCE_MS", "not-a-number")
    monkeypatch.setenv("STT_VAD_THRESHOLD", "not-a-number")
    reloaded = _reload_config()
    try:
        assert reloaded.STT_VAD_SILENCE_MS == 500
        assert reloaded.STT_VAD_THRESHOLD == 0.5
    finally:
        _reload_config()
