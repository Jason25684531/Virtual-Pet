import importlib
import os
import sys
from pathlib import Path

import config

KEYS = ("FASTEMBED_CACHE_PATH", "HF_HUB_OFFLINE", "PLAYWRIGHT_BROWSERS_PATH", "STT_MODEL_PATH", "STT_VAD_MODEL_DIR", "OLLAMA_MODEL")


def _load(tmp_path, monkeypatch, *dirs, env: dict[str, str] | None = None, files: dict[str, str] | None = None):
    """把 config.py 複製到暫存 APP_ROOT 後重新載入，模擬 Release 目錄。"""
    # config.py 用 os.environ.setdefault 寫入；換成副本，測試結束後才不會把 HF_HUB_OFFLINE /
    # PLAYWRIGHT_BROWSERS_PATH 漏給後面的測試（monkeypatch.delenv 不會記錄原本不存在的 key）。
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k not in KEYS})
    for name in dirs:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    (tmp_path / "config.py").write_bytes(Path(config.__file__).read_bytes())
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "config", raising=False)
    module = importlib.import_module("config")
    assert Path(module.__file__).parent == tmp_path
    return module


def teardown_function():
    sys.modules["config"] = config


def test_bundled_models_dir_is_default_and_offline(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, "models", "ms-playwright")
    assert Path(cfg.STT_MODEL_PATH) == tmp_path / "models" / "whisper"
    assert Path(cfg.STT_VAD_MODEL_DIR) == tmp_path / "models" / "vad"
    assert Path(os.environ["FASTEMBED_CACHE_PATH"]) == tmp_path / "models" / "fastembed"
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) == tmp_path / "ms-playwright"


def test_environment_overrides_bundled_models(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, "models", env={"STT_MODEL_PATH": "X:/whisper", "FASTEMBED_CACHE_PATH": "X:/fe", "HF_HUB_OFFLINE": "0"})
    assert cfg.STT_MODEL_PATH == "X:/whisper"
    assert os.environ["FASTEMBED_CACHE_PATH"] == "X:/fe"
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_without_models_dir_defaults_are_unchanged(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch)
    assert Path(cfg.STT_MODEL_PATH) == tmp_path / "runtime_cache" / "whisper"
    assert Path(cfg.STT_VAD_MODEL_DIR) == tmp_path / "runtime_cache" / "vad"
    assert "FASTEMBED_CACHE_PATH" not in os.environ and "HF_HUB_OFFLINE" not in os.environ


def test_config_dir_env_takes_priority(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, files={"config/.env": "OLLAMA_MODEL=from-config-dir\n", ".env": "OLLAMA_MODEL=from-root\n"})
    assert cfg.ENV_PATH == tmp_path / "config" / ".env"
    assert os.environ["OLLAMA_MODEL"] == "from-config-dir"


def test_root_env_is_fallback(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, files={".env": "OLLAMA_MODEL=from-root\n"})
    assert cfg.ENV_PATH == tmp_path / ".env"
    assert os.environ["OLLAMA_MODEL"] == "from-root"
