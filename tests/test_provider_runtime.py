"""任務 1.4:ProviderRuntime 單元測試 — API/Ollama、缺 key、端點不健康、adapter replacement。"""

import json

import pytest

from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.runtime.provider_runtime import (
    ProviderRuntime,
    UnavailableProvider,
    migrate_legacy_provider_config,
)
from pet_harness.storage.sqlite_store import SQLiteStore


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _api_ok_request_fn(url=None, headers=None, json=None, timeout=None):
    return _Resp({"choices": [{"message": {"content": "real api reply"}}]})


def _ollama_ok_request_fn(method, url, timeout=None, json=None):
    if url.endswith("/api/tags"):
        return _Resp({"models": [{"name": "llama3"}]})
    return _Resp({"response": "real ollama reply"})


def _ollama_down_request_fn(method=None, url=None, timeout=None, json=None, **kwargs):
    raise ConnectionError("endpoint down")


def _event(text="hello"):
    return UserEvent.from_dict({"text": text, "source": "test"})


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "runtime" / "provider_config.json"


def test_unconfigured_runtime_is_fail_closed(config_path):
    runtime = ProviderRuntime(config_path=config_path)

    status = runtime.get_status()
    assert status.healthy is False
    assert status.provider_type is None
    assert isinstance(runtime.get_provider(), UnavailableProvider)

    reply = runtime.generate_reply(_event())
    assert reply.provider_status.healthy is False
    assert reply.reply.startswith("AI provider unavailable")


def test_api_missing_key_returns_unavailable_not_fake(config_path, monkeypatch):
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    runtime = ProviderRuntime(config_path=config_path)
    status = runtime.configure(
        ProviderConfig(provider_type=ProviderType.API, base_url="https://x", api_key_env_var="TEST_MISSING_KEY")
    )

    assert status.healthy is False
    assert status.metadata["error_category"] == "missing_api_key"
    reply = runtime.generate_reply(_event())
    assert reply.provider_status.healthy is False
    assert "unavailable" in reply.reply
    # 不得偽造 mock 回覆
    assert "[mock]" not in reply.reply and "[low_spec]" not in reply.reply


def test_api_with_key_is_healthy_and_replies(config_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-secret-value-123456")
    runtime = ProviderRuntime(config_path=config_path, request_fn=_api_ok_request_fn)
    status = runtime.configure(
        ProviderConfig(
            provider_type=ProviderType.API,
            base_url="https://api.example/v1/chat/completions",
            model_name="test-model",
            api_key_env_var="TEST_API_KEY",
        )
    )

    assert status.healthy is True
    assert status.provider_type is ProviderType.API
    reply = runtime.generate_reply(_event())
    assert reply.reply == "real api reply"
    assert reply.provider_status.healthy is True


def test_ollama_unhealthy_endpoint_reports_unavailable(config_path):
    runtime = ProviderRuntime(config_path=config_path, request_fn=_ollama_down_request_fn)
    status = runtime.configure(
        ProviderConfig(provider_type=ProviderType.OLLAMA, base_url="http://localhost:11434")
    )

    assert status.healthy is False
    reply = runtime.generate_reply(_event())
    assert reply.provider_status.healthy is False
    assert reply.reply.startswith("AI provider unavailable")


def test_ollama_healthy_generates_reply(config_path):
    runtime = ProviderRuntime(config_path=config_path, request_fn=_ollama_ok_request_fn)
    status = runtime.configure(
        ProviderConfig(provider_type=ProviderType.OLLAMA, base_url="http://localhost:11434", model_name="llama3")
    )

    assert status.healthy is True
    reply = runtime.generate_reply(_event())
    assert reply.reply == "real ollama reply"


def test_configure_replaces_adapter_for_next_request(config_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-secret-value-123456")
    runtime = ProviderRuntime(config_path=config_path, request_fn=_api_ok_request_fn)
    runtime.configure(
        ProviderConfig(provider_type=ProviderType.API, base_url="https://x", api_key_env_var="TEST_API_KEY")
    )
    old_adapter = runtime.get_provider()

    runtime.configure(ProviderConfig(provider_type=ProviderType.OLLAMA, base_url="http://localhost:11434"))

    # 進行中的請求保留其已取得的 adapter;下一個請求取得新 adapter。
    assert runtime.get_provider() is not old_adapter
    assert old_adapter.generate_reply(_event()).reply == "real api reply"


def test_status_payload_never_exposes_secret(config_path, monkeypatch):
    secret = "sk-super-secret-value-987654"
    monkeypatch.setenv("TEST_API_KEY", secret)
    runtime = ProviderRuntime(config_path=config_path, request_fn=_api_ok_request_fn)
    runtime.configure(
        ProviderConfig(provider_type=ProviderType.API, base_url="https://x", api_key_env_var="TEST_API_KEY")
    )

    payload = runtime.status_payload()
    assert secret not in json.dumps(payload)
    assert payload["api_key_status"] == "configured"


def test_config_round_trips_to_disk(config_path):
    runtime = ProviderRuntime(config_path=config_path, request_fn=_ollama_ok_request_fn)
    runtime.configure(
        ProviderConfig(provider_type=ProviderType.OLLAMA, base_url="http://localhost:11434", model_name="llama3")
    )

    reloaded = ProviderRuntime(config_path=config_path, request_fn=_ollama_ok_request_fn)
    config = reloaded.get_config()
    assert config is not None
    assert config.provider_type is ProviderType.OLLAMA
    assert config.model_name == "llama3"


def test_migration_promotes_character_provider_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-secret-value-123456")
    char_dir = tmp_path / "characters" / "Choppr"
    char_dir.mkdir(parents=True)
    store = SQLiteStore(char_dir / "state.db")
    store.initialize()
    legacy = {
        "provider_type": "api",
        "base_url": "https://legacy.example",
        "model_name": "legacy-model",
        "api_key_env_var": "TEST_API_KEY",
    }
    store.set_setting("provider_config", legacy)

    runtime = ProviderRuntime(config_path=tmp_path / "runtime" / "provider_config.json", request_fn=_api_ok_request_fn)
    diagnostics = migrate_legacy_provider_config(runtime, characters_data_dir=tmp_path / "characters")

    assert diagnostics["migrated_from"].endswith("state.db")
    config = runtime.get_config()
    assert config.provider_type is ProviderType.API
    assert config.model_name == "legacy-model"
    # 非破壞性:舊值保留,rollback 只需回復程式版本。
    assert store.get_setting("provider_config") == legacy


def test_migration_ignores_legacy_mock_config(tmp_path):
    char_dir = tmp_path / "characters" / "Choppr"
    char_dir.mkdir(parents=True)
    store = SQLiteStore(char_dir / "state.db")
    store.initialize()
    store.set_setting("provider_config", {"provider_type": "mock"})

    runtime = ProviderRuntime(config_path=tmp_path / "runtime" / "provider_config.json")
    diagnostics = migrate_legacy_provider_config(runtime, characters_data_dir=tmp_path / "characters")

    assert diagnostics["migrated_from"] is None
    assert diagnostics["skipped"]
    assert runtime.get_config() is None
