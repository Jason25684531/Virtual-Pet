"""switch-api-to-ollama 任務 2:啟動時預設 Ollama、model 預設值、不健康時 fail-closed。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.ui.pyqt_harness_adapter import DEFAULT_OLLAMA_MODEL, PyQtHarnessAdapter

from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _ollama_ok_request_fn(method, url, timeout=None, json=None):
    if url.endswith("/api/tags"):
        return _Resp({"models": [{"name": DEFAULT_OLLAMA_MODEL}]})
    return _Resp({"response": "real ollama reply"})


def _ollama_down_request_fn(method=None, url=None, timeout=None, json=None, **kwargs):
    raise ConnectionError("endpoint down")


def _api_ok_request_fn(url=None, headers=None, json=None, timeout=None):
    return _Resp({"choices": [{"message": {"content": "real api reply"}}]})


def _event(text="hello"):
    return UserEvent.from_dict({"text": text, "source": "test"})


def test_bootstrap_defaults_to_ollama_even_with_api_key_present(harness_env, monkeypatch):
    tmp_path, agentic_root = harness_env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-matter")
    runtime = ProviderRuntime(
        config_path=tmp_path / "runtime" / "provider_config.json",
        request_fn=_ollama_ok_request_fn,
    )

    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=runtime,
    )

    config = adapter.provider_runtime.get_config()
    assert config is not None
    assert config.provider_type is ProviderType.OLLAMA
    assert adapter.provider_runtime.get_status().healthy is True


def test_bootstrap_respects_persisted_api_selection(harness_env):
    tmp_path, agentic_root = harness_env
    config_path = tmp_path / "runtime" / "provider_config.json"

    # 先持久化一份 api 設定,模擬「上次已選 api」。
    seed_runtime = ProviderRuntime(config_path=config_path, request_fn=_api_ok_request_fn)
    seed_runtime.configure(
        ProviderConfig(
            provider_type=ProviderType.API,
            base_url="https://api.example/v1/chat/completions",
            model_name="gpt-4o-mini",
            api_key_env_var="OPENAI_API_KEY",
        )
    )

    # 新 runtime 從磁碟載入既有設定(不是 test-injected),bootstrap 應只 refresh。
    reloaded_runtime = ProviderRuntime(config_path=config_path, request_fn=_api_ok_request_fn)
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=reloaded_runtime,
    )

    config = adapter.provider_runtime.get_config()
    assert config.provider_type is ProviderType.API
    assert config.model_name == "gpt-4o-mini"


def test_ollama_model_defaults_and_env_override(harness_env):
    tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(
            config_path=tmp_path / "runtime" / "provider_config.json",
            request_fn=_ollama_ok_request_fn,
        ),
    )

    default_config = adapter.build_provider_config("ollama")
    assert default_config.model_name == DEFAULT_OLLAMA_MODEL

    (tmp_path / ".env").write_text("OLLAMA_MODEL=qwen3:8b\n", encoding="utf-8")
    adapter._project_env = adapter._load_project_env()
    overridden_config = adapter.build_provider_config("ollama")
    assert overridden_config.model_name == "qwen3:8b"


def test_bootstrap_ollama_down_is_unhealthy_and_does_not_fall_back_to_api(harness_env, monkeypatch):
    tmp_path, agentic_root = harness_env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-matter")
    runtime = ProviderRuntime(
        config_path=tmp_path / "runtime" / "provider_config.json",
        request_fn=_ollama_down_request_fn,
    )

    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=runtime,
    )

    status = adapter.provider_runtime.get_status()
    assert status.provider_type is ProviderType.OLLAMA
    assert status.healthy is False

    reply = adapter.provider_runtime.generate_reply(_event())
    assert reply.provider_status.healthy is False
    assert reply.reply.startswith("AI provider unavailable")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
