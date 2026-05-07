from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
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
        self.properties: dict[object, str] = {}

    def set_property(self, property_id, value: str):
        self.properties[property_id] = value


class FakeAudioConfig:
    def __init__(self, use_default_microphone: bool = True):
        self.use_default_microphone = use_default_microphone


class FakeRecognizer:
    def __init__(self, speech_sdk):
        self._speech_sdk = speech_sdk
        self.speech_start_detected = FakeSignal()
        self.speech_end_detected = FakeSignal()
        self.recognizing = FakeSignal()
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
            self.speech_start_detected.emit(SimpleNamespace())
            self.recognizing.emit(
                SimpleNamespace(
                    result=SimpleNamespace(
                        reason=self._speech_sdk.ResultReason.RecognizingSpeech,
                        text="哈囉",
                    )
                )
            )
            self.speech_end_detected.emit(SimpleNamespace())
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
        RecognizingSpeech = "RecognizingSpeech"
        RecognizedSpeech = "RecognizedSpeech"
        NoMatch = "NoMatch"

    class CancellationDetails:
        def __init__(self, result):
            self.reason = getattr(result, "cancellation_reason", None)
            self.error_details = getattr(result, "cancellation_error_details", None)
            self.error_code = getattr(result, "cancellation_error_code", None)

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

    def test_recognizing_event_emits_preview_only(self):
        speech_sdk = FakeSpeechSDK()
        partials: list[str] = []
        recognized: list[str] = []
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognizing_text.connect(partials.append)
        worker.recognized_text.connect(recognized.append)

        worker._handle_recognizing_event(
            SimpleNamespace(result=SimpleNamespace(text="  還在辨識中  "))
        )

        self.assertEqual(partials, ["還在辨識中"])
        self.assertEqual(recognized, [])

    def test_recognized_signal_can_bind_directly_to_brain_input(self):
        speech_sdk = FakeSpeechSDK()
        brain_input = FakeBrainInput()
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognized_text.connect(brain_input.send_to_brain)

        worker._emit_recognized_text("直接送進 BrainEngine")

        self.assertEqual(brain_input.messages, ["直接送進 BrainEngine"])

    def test_stop_requested_suppresses_late_recognized_events(self):
        speech_sdk = FakeSpeechSDK()
        recognized: list[str] = []
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognized_text.connect(recognized.append)
        partials: list[str] = []
        worker.recognizing_text.connect(partials.append)

        worker.stop()
        worker._handle_recognized_event(
            SimpleNamespace(
                result=SimpleNamespace(
                    reason=speech_sdk.ResultReason.RecognizedSpeech,
                    text="停止後不應送出",
                )
            )
        )

        self.assertEqual(recognized, [])

    def test_canceled_event_prefers_sdk_cancellation_details(self):
        speech_sdk = FakeSpeechSDK()
        warnings: list[str] = []
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.warning_emitted.connect(warnings.append)

        worker._handle_canceled_event(
            SimpleNamespace(
                reason=None,
                error_details=None,
                result=SimpleNamespace(
                    cancellation_reason="CancellationReason.Error",
                    cancellation_error_details="Could not validate speech context.",
                    cancellation_error_code="1007",
                ),
            )
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("CancellationReason.Error", warnings[0])
        self.assertIn("error_code=1007", warnings[0])
        self.assertIn("Could not validate speech context.", warnings[0])

    def test_run_starts_and_stops_continuous_recognition_safely(self):
        speech_sdk = FakeSpeechSDK()
        recognized: list[str] = []
        partials: list[str] = []
        speech_events: list[str] = []
        statuses: list[str] = []
        listening_states: list[bool] = []
        worker = AzureSTTWorker(api_key="key", region="eastus", speech_sdk=speech_sdk)
        worker.recognized_text.connect(recognized.append)
        worker.recognizing_text.connect(partials.append)
        worker.speech_started.connect(lambda: speech_events.append("start"))
        worker.speech_ended.connect(lambda: speech_events.append("end"))
        worker.status_changed.connect(statuses.append)
        worker.listening_state_changed.connect(listening_states.append)

        worker.run()

        recognizer = speech_sdk.last_recognizer
        self.assertIsNotNone(recognizer)
        self.assertTrue(recognizer.started)
        self.assertTrue(recognizer.stopped)
        self.assertEqual(recognized, ["哈囉 ECHOES"])
        self.assertEqual(partials, ["哈囉"])
        self.assertEqual(speech_events, ["start", "end"])
        self.assertTrue(any("開始接收麥克風音訊" in status for status in statuses))
        self.assertTrue(any("工作階段已停止" in status for status in statuses))
        self.assertEqual(listening_states[:2], [True, False])
        self.assertEqual(
            recognizer.speech_config.properties.get(
                speech_sdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs
            ),
            str(config.AZURE_STT_INITIAL_SILENCE_TIMEOUT_MS),
        )
        self.assertEqual(
            recognizer.speech_config.properties.get(
                speech_sdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs
            ),
            str(config.AZURE_STT_END_SILENCE_TIMEOUT_MS),
        )
        self.assertEqual(
            recognizer.speech_config.properties.get(
                speech_sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs
            ),
            str(config.AZURE_STT_SEGMENTATION_SILENCE_TIMEOUT_MS),
        )
        self.assertNotIn(
            speech_sdk.PropertyId.Speech_SegmentationMaximumTimeMs,
            recognizer.speech_config.properties,
        )


FakeSpeechSDK.PropertyId = SimpleNamespace(
    SpeechServiceConnection_InitialSilenceTimeoutMs="initial_silence",
    SpeechServiceConnection_EndSilenceTimeoutMs="end_silence",
    Speech_SegmentationSilenceTimeoutMs="segmentation_silence",
    Speech_SegmentationMaximumTimeMs="segmentation_max_time",
)


if __name__ == "__main__":
    unittest.main()
