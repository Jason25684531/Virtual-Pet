from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sensors.microphone_stt import AzureSTTWorker


class FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, event):
        for callback in list(self._callbacks):
            callback(event)


class FakeAsyncResult:
    def __init__(self, on_get=None):
        self._on_get = on_get or (lambda: None)

    def get(self):
        self._on_get()


class FakeSpeechConfig:
    def __init__(self, subscription: str, region: str):
        self.subscription = subscription
        self.region = region
        self.speech_recognition_language = ""


class FakeAudioConfig:
    def __init__(self, use_default_microphone: bool = True):
        self.use_default_microphone = use_default_microphone


class FakeRecognizer:
    def __init__(self, speech_sdk):
        self._speech_sdk = speech_sdk
        self.recognized = FakeSignal()
        self.canceled = FakeSignal()
        self.session_started = FakeSignal()
        self.session_stopped = FakeSignal()
        self.started = False
        self.stopped = False

    def start_continuous_recognition_async(self):
        def on_get():
            self.started = True
            self.session_started.emit(SimpleNamespace())
            self.recognized.emit(
                SimpleNamespace(
                    result=SimpleNamespace(
                        reason=self._speech_sdk.ResultReason.RecognizedSpeech,
                        text="哈囉 ECHOES",
                    )
                )
            )
            self.session_stopped.emit(SimpleNamespace())

        return FakeAsyncResult(on_get=on_get)

    def stop_continuous_recognition_async(self):
        return FakeAsyncResult(on_get=self._mark_stopped)

    def _mark_stopped(self):
        self.stopped = True


class FakeSpeechSDK:
    class ResultReason:
        RecognizedSpeech = "RecognizedSpeech"
        NoMatch = "NoMatch"

    class audio:
        AudioConfig = FakeAudioConfig

    SpeechConfig = FakeSpeechConfig

    def __init__(self):
        self.last_recognizer = None

    def SpeechRecognizer(self, speech_config, audio_config):
        self.last_recognizer = FakeRecognizer(self)
        self.last_recognizer.speech_config = speech_config
        self.last_recognizer.audio_config = audio_config
        return self.last_recognizer


class FakeBrainInput:
    def __init__(self):
        self.messages: list[str] = []

    def send_to_brain(self, text: str):
        self.messages.append(text)
        return True


class AzureSTTWorkerTests(unittest.TestCase):
    def test_missing_config_emits_warning_without_crashing(self):
        warnings: list[str] = []
        listening_states: list[bool] = []
        worker = AzureSTTWorker(api_key="", region="", speech_sdk=object())
        worker.warning_emitted.connect(warnings.append)
        worker.listening_state_changed.connect(listening_states.append)

        worker.run()

        self.assertEqual(len(warnings), 1)
        self.assertIn("缺少 AZURE_STT_API_KEY 或 AZURE_STT_REGION", warnings[0])
        self.assertEqual(listening_states, [False])

    def test_recognized_event_only_emits_non_empty_text(self):
        speech_sdk = FakeSpeechSDK()
        recognized: list[str] = []
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognized_text.connect(recognized.append)

        worker._handle_recognized_event(
            SimpleNamespace(
                result=SimpleNamespace(
                    reason=speech_sdk.ResultReason.RecognizedSpeech,
                    text="  測試語音  ",
                )
            )
        )
        worker._handle_recognized_event(
            SimpleNamespace(
                result=SimpleNamespace(
                    reason=speech_sdk.ResultReason.RecognizedSpeech,
                    text="   ",
                )
            )
        )
        worker._handle_recognized_event(
            SimpleNamespace(
                result=SimpleNamespace(
                    reason=speech_sdk.ResultReason.NoMatch,
                    text="不應送出",
                )
            )
        )

        self.assertEqual(recognized, ["測試語音"])

    def test_recognized_signal_can_bind_directly_to_brain_input(self):
        speech_sdk = FakeSpeechSDK()
        brain_input = FakeBrainInput()
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognized_text.connect(brain_input.send_to_brain)

        worker._emit_recognized_text("直接送進 BrainEngine")

        self.assertEqual(brain_input.messages, ["直接送進 BrainEngine"])

    def test_run_starts_and_stops_continuous_recognition_safely(self):
        speech_sdk = FakeSpeechSDK()
        recognized: list[str] = []
        statuses: list[str] = []
        listening_states: list[bool] = []
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognized_text.connect(recognized.append)
        worker.status_changed.connect(statuses.append)
        worker.listening_state_changed.connect(listening_states.append)

        worker.run()

        recognizer = speech_sdk.last_recognizer
        self.assertIsNotNone(recognizer)
        self.assertTrue(recognizer.started)
        self.assertTrue(recognizer.stopped)
        self.assertEqual(recognized, ["哈囉 ECHOES"])
        self.assertTrue(any("開始接收麥克風音訊" in status for status in statuses))
        self.assertTrue(any("工作階段已停止" in status for status in statuses))
        self.assertEqual(listening_states[:2], [True, False])


if __name__ == "__main__":
    unittest.main()
