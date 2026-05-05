from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import connect_brain_output_handlers


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeBrain:
    def __init__(self):
        self.token_streamed = _FakeSignal()
        self.streamed_fragment = _FakeSignal()
        self.sentence_ready = _FakeSignal()
        self.speech_ready = _FakeSignal()


class _FakeWindow:
    def __init__(self):
        self.append_calls: list[tuple[str, str]] = []
        self.dispatch_calls: list[tuple[str, str | None, bool]] = []
        self.speak_calls: list[tuple[str, str | None]] = []

    def append_conversation_assistant(self, trace_id: str, text: str):
        self.append_calls.append((trace_id, text))

    def dispatch_action(self, directive: str, trace_id: str | None = None, allow_tts: bool = True):
        self.dispatch_calls.append((directive, trace_id, allow_tts))
        return True

    def speak_text(self, message: str, trace_id: str | None = None, has_action: bool = False):
        del has_action
        self.speak_calls.append((message, trace_id))


def _sanitize_text(text: str) -> str:
    return str(text or "").replace("[ACTION:listen]", "").strip()


class MainRuntimeWiringTests(unittest.TestCase):
    def test_token_streamed_drives_ui_while_sentence_ready_triggers_tts(self):
        window = _FakeWindow()
        brain = _FakeBrain()

        connect_brain_output_handlers(window, brain, _sanitize_text)

        brain.streamed_fragment.emit("[ACTION:listen]", "trace-1")
        brain.token_streamed.emit("第一段。", "trace-1")
        brain.streamed_fragment.emit("第一段。", "trace-1")
        brain.token_streamed.emit("第二段。", "trace-1")
        brain.streamed_fragment.emit("第二段。", "trace-1")
        brain.sentence_ready.emit("第一段。", "trace-1")
        brain.sentence_ready.emit("第二段。", "trace-1")

        self.assertEqual(
            window.append_calls,
            [("trace-1", "第一段。"), ("trace-1", "第二段。")],
        )
        self.assertEqual(
            window.dispatch_calls,
            [
                ("[ACTION:listen]", "trace-1", False),
                ("第一段。", "trace-1", False),
                ("第二段。", "trace-1", False),
            ],
        )
        self.assertEqual(window.speak_calls, [("第一段。", "trace-1"), ("第二段。", "trace-1")])


if __name__ == "__main__":
    unittest.main()
