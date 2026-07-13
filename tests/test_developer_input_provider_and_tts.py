"""Regression tests: text submission carries text only, and replies must reach TTS."""

import inspect

import pytest
from unittest.mock import MagicMock

from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from ui.transparent_window import TransparentWindow

from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)
from tests.conftest import FakeProvider
from pet_harness.runtime.provider_runtime import ProviderRuntime


def test_handle_text_input_accepts_text_only(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    # 提交簽名不得再接受 provider 覆寫參數
    assert list(inspect.signature(adapter.handle_text_input).parameters) == ["text"]

    payload = adapter.handle_text_input("hello")
    assert payload["reply"].startswith("[fake]")


def test_handle_text_input_rejects_provider_keyword(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    with pytest.raises(TypeError):
        adapter.handle_text_input("hello", provider="api")


def test_get_provider_status_does_not_crash(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    status = adapter.get_provider_status()
    assert status["active_character_id"] == "Choppr"
    assert status["ai"]["provider"] is not None
    assert "tts" in status and "stt" in status


def test_on_agentic_result_speaks_nonempty_reply():
    fake_self = MagicMock()

    TransparentWindow._on_agentic_result(fake_self, {"reply": "hello there", "webm_key": "idle"})

    fake_self.speak_text.assert_called_once()
    args, kwargs = fake_self.speak_text.call_args
    assert args == ("hello there",)
    assert kwargs["has_action"] is True
    assert kwargs["trace_id"]  # non-empty trace_id required by PCM session playback


def test_on_agentic_result_skips_empty_reply():
    fake_self = MagicMock()

    TransparentWindow._on_agentic_result(fake_self, {"reply": "   ", "webm_key": ""})

    fake_self.speak_text.assert_not_called()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
