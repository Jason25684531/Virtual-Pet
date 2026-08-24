from __future__ import annotations

from threading import Event

from pet_harness.agent.api_provider import APIProvider
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType


class _FakeSseResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self.status_code = status_code
        self._lines = lines
        self.closed = False

    def iter_lines(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


def _config(**overrides) -> ProviderConfig:
    base = dict(
        provider_type=ProviderType.API,
        base_url="https://api.openai.com/v1/chat/completions",
        model_name="gpt-4o-mini",
        api_key_env_var=None,
    )
    base.update(overrides)
    return ProviderConfig(**base)


def _event(text: str = "hello") -> UserEvent:
    return UserEvent.from_dict({"text": text, "source": "test"})


def test_generate_reply_stream_yields_deltas_and_stops_at_done():
    response = _FakeSseResponse([
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"never yielded"}}]}',
    ])
    provider = APIProvider(_config(), request_fn=lambda **_kwargs: response)

    fragments = list(provider.generate_reply_stream(_event()))

    assert fragments == ["Hello", " world"]
    assert response.closed is True


def test_generate_reply_stream_returns_none_on_http_error_status():
    response = _FakeSseResponse([], status_code=500)
    provider = APIProvider(_config(), request_fn=lambda **_kwargs: response)

    assert provider.generate_reply_stream(_event()) is None


def test_generate_reply_stream_returns_none_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)
    provider = APIProvider(_config(api_key_env_var="MISSING_TEST_KEY"), request_fn=lambda **_kwargs: _FakeSseResponse([]))

    assert provider.generate_reply_stream(_event()) is None


def test_generate_reply_stream_returns_none_on_request_exception():
    def _raise(**_kwargs):
        raise ConnectionError("boom")

    provider = APIProvider(_config(), request_fn=_raise)

    assert provider.generate_reply_stream(_event()) is None


def test_generate_reply_stream_stops_early_when_cancelled():
    response = _FakeSseResponse([
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
    ])
    provider = APIProvider(_config(), request_fn=lambda **_kwargs: response)
    cancel = Event()
    cancel.set()

    fragments = list(provider.generate_reply_stream(_event(), cancel=cancel))

    assert fragments == []


def test_non_streaming_payload_unaffected_by_stream_support():
    """_build_payload(stream=False) must stay byte-identical to before — no stray "stream" key."""
    provider = APIProvider(_config())

    assert provider._build_payload("hi") == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert provider._build_payload("hi", stream=True)["stream"] is True
