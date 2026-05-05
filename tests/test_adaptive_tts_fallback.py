from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PyQt5.QtCore import QCoreApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker


class _SignalCollector:
    def __init__(self):
        self.events = []

    def __call__(self, *args):
        self.events.append(args)


class _DebugSignal:
    def __init__(self):
        self._callbacks: list[object] = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _VoAIFastFailWorker:
    instances: list["_VoAIFastFailWorker"] = []

    def __init__(self, *args, adaptive_fallback_enabled: bool = False, **kwargs):
        self.reply_id = kwargs.get("reply_id") or "reply-fast-fail"
        self.trace_id = kwargs.get("trace_id") or "trace-fast-fail"
        self.adaptive_fallback_enabled = adaptive_fallback_enabled
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        _VoAIFastFailWorker.instances.append(self)

    def start(self):
        self.finished_signal.emit(
            False,
            "VoAI 529",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "voai",
                "fast_fail": True,
                "failure_code": "http_529",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


class _ElevenLabsSuccessWorker:
    instances: list["_ElevenLabsSuccessWorker"] = []

    def __init__(self, *args, **kwargs):
        self.reply_id = kwargs.get("reply_id") or "reply-success"
        self.trace_id = kwargs.get("trace_id") or "trace-success"
        self.voice_id = kwargs.get("voice_id") or ""
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        _ElevenLabsSuccessWorker.instances.append(self)

    def start(self):
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
            },
        )
        self.finished_signal.emit(
            True,
            "ElevenLabs success",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
                "selected_provider": "elevenlabs",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


class _ElevenLabsFailureWorker:
    instances: list["_ElevenLabsFailureWorker"] = []

    def __init__(self, *args, **kwargs):
        self.reply_id = kwargs.get("reply_id") or "reply-eleven-fail"
        self.trace_id = kwargs.get("trace_id") or "trace-eleven-fail"
        self.finished_signal = _DebugSignal()
        self.progress_signal = _DebugSignal()
        self.audio_ready_signal = _DebugSignal()
        self.finished = _DebugSignal()
        _ElevenLabsFailureWorker.instances.append(self)

    def start(self):
        self.finished_signal.emit(
            False,
            "ElevenLabs down",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


class AdaptiveTTSFallbackWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        _VoAIFastFailWorker.instances.clear()
        _ElevenLabsSuccessWorker.instances.clear()
        _ElevenLabsFailureWorker.instances.clear()

    def test_driver_started_is_forwarded_after_fast_fail_fallback(self):
        progress = _SignalCollector()
        finished = _SignalCollector()
        worker = AdaptiveTTSFallbackWorker(
            text="測試",
            reply_id="reply-1",
            trace_id="trace-1",
            voice_id="Choppr",
            fallback_voice_id="ELV-VOICE-1",
            voai_worker_factory=_VoAIFastFailWorker,
            elevenlabs_worker_factory=_ElevenLabsSuccessWorker,
        )
        worker.progress_signal.connect(progress)
        worker.finished_signal.connect(finished)

        worker.start()

        self.assertTrue(_VoAIFastFailWorker.instances[0].adaptive_fallback_enabled)
        event_names = [event[0] for event in progress.events]
        self.assertEqual(event_names[0], "provider_selected")
        self.assertIn("fallback_triggered", event_names)
        self.assertEqual(event_names[-2], "provider_selected")
        self.assertEqual(event_names[-1], "driver_started")
        self.assertTrue(finished.events[0][0])
        self.assertEqual(finished.events[0][2]["selected_provider"], "elevenlabs")
        self.assertEqual(_ElevenLabsSuccessWorker.instances[0].voice_id, "ELV-VOICE-1")

    def test_double_failure_emits_critical_text_only_payload(self):
        progress = _SignalCollector()
        finished = _SignalCollector()
        worker = AdaptiveTTSFallbackWorker(
            text="測試",
            reply_id="reply-2",
            trace_id="trace-2",
            voice_id="Choppr",
            fallback_voice_id="ELV-VOICE-2",
            voai_worker_factory=_VoAIFastFailWorker,
            elevenlabs_worker_factory=_ElevenLabsFailureWorker,
        )
        worker.progress_signal.connect(progress)
        worker.finished_signal.connect(finished)

        worker.start()

        event_names = [event[0] for event in progress.events]
        self.assertIn("critical_tts_failure", event_names)
        self.assertFalse(finished.events[0][0])
        payload = finished.events[0][2]
        self.assertTrue(payload["critical_tts_failure"])
        self.assertTrue(payload["text_only"])
        self.assertEqual(payload["provider_chain"], ["voai", "elevenlabs"])


if __name__ == "__main__":
    unittest.main()
