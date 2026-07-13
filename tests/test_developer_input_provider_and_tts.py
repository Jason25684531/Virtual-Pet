"""Regression tests: D-key text submission must not force mock, and replies must reach TTS."""

from unittest.mock import MagicMock

from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from ui.transparent_window import TransparentWindow

from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)


def test_handle_text_input_without_provider_does_not_override(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(default_character_id="Choppr", agentic_root=str(agentic_root))
    adapter._set_provider = MagicMock(wraps=adapter._set_provider)

    adapter.handle_text_input("hello")

    adapter._set_provider.assert_not_called()


def test_handle_text_input_with_explicit_provider_overrides(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(default_character_id="Choppr", agentic_root=str(agentic_root))
    adapter._set_provider = MagicMock(wraps=adapter._set_provider)

    adapter.handle_text_input("hello", provider="mock")

    adapter._set_provider.assert_called_once_with("mock")


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
