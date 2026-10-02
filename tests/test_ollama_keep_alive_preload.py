"""reduce-turn-latency 1.x:keep_alive 常駐與啟動預載。"""

from __future__ import annotations

from types import SimpleNamespace

import config
from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType


def _provider(request_fn):
    cfg = ProviderConfig(provider_type=ProviderType.OLLAMA, model_name="m", base_url="http://x", timeout_seconds=1)
    return OllamaProvider(cfg, request_fn=request_fn)


def test_keep_alive_value_int_vs_duration(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_KEEP_ALIVE", "-1")
    assert config.ollama_keep_alive_value() == -1 and isinstance(config.ollama_keep_alive_value(), int)
    monkeypatch.setattr(config, "OLLAMA_KEEP_ALIVE", "10m")
    assert config.ollama_keep_alive_value() == "10m"


def test_both_generate_paths_send_keep_alive():
    seen = []

    def request_fn(method, url, timeout, json=None, stream=False):
        seen.append(json)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"response": "hi"},
            iter_lines=lambda: [b'{"response":"hi","done":true}'],
            close=lambda: None,
        )

    provider = _provider(request_fn)
    event = UserEvent(text="yo")
    provider.generate_reply(event)
    list(provider.generate_reply_stream(event))
    assert len(seen) == 2 and all(p["keep_alive"] == config.ollama_keep_alive_value() for p in seen)


def test_preload_sends_no_prompt_and_reports_success():
    seen = []
    ok = _provider(lambda m, u, timeout, json=None, stream=False: seen.append(json) or SimpleNamespace(status_code=200))
    assert ok.preload() is True
    assert "prompt" not in seen[0] and seen[0]["model"] == "m" and "keep_alive" in seen[0]


def test_preload_failure_is_swallowed():
    def boom(*_a, **_k):
        raise ConnectionError("down")

    assert _provider(boom).preload() is False


def test_provider_runtime_preload_delegates_to_current_adapter():
    from types import SimpleNamespace as NS

    from pet_harness.runtime.provider_runtime import ProviderRuntime

    assert ProviderRuntime(provider=NS(preload=lambda: True)).preload() is True
    assert ProviderRuntime(provider=NS()).preload() is False  # 無預載能力(如 API provider)


def test_404_names_the_missing_model_and_the_fix():
    provider = _provider(lambda *a, **k: SimpleNamespace(status_code=404))
    assert "ollama pull m" in provider._http_error(404)
    assert provider._http_error(500) == "Ollama returned status 500."


def test_default_ollama_model_is_the_one_actually_pulled():
    assert config.DEFAULT_OLLAMA_MODEL == "gemma3:12b"
