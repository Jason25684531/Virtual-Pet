from __future__ import annotations

import tempfile
import sys
import time
from pathlib import Path
import unittest

from PyQt5.QtCore import QCoreApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from action_dispatcher import ActionDispatcher
from character_library import CharacterLibrary
from main import build_wave_response_directive
from sensors.camera_vision import (
    WAVE_RESPONSE_DIRECTIVE,
    WaveDetectionConfig,
    WaveSensor,
    cv2,
)


TEST_CHARACTER_ID = "miku"


class FakeCapture:
    def __init__(self, frames: list[object], opened: bool = True):
        self._frames = list(frames)
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def set(self, *_args):
        return True

    def read(self):
        if not self._frames:
            return False, None
        return True, self._frames.pop(0)

    def release(self):
        self.released = True


class _DebugSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _ImmediateWaveWorker:
    def __init__(self, success: bool = True, message: str = "", payload: object | None = None, parent=None):
        del parent
        self._success = success
        self._message = message
        self._payload = payload
        self.finished_signal = _DebugSignal()
        self.finished = _DebugSignal()

    def start(self):
        self.finished_signal.emit(self._success, self._message, self._payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _WaveProbeWindow:
    def __init__(self):
        self.status_calls: list[tuple[str, str, int]] = []
        self.motion_calls: list[str] = []
        self.motion_asset_calls: list[tuple[str, str, bool]] = []
        self.motion_loop_calls: list[tuple[str, int]] = []
        self.audio_calls: list[tuple[str, str, bool]] = []
        self.restore_idle_calls = 0

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.motion_calls.append(motion_key)
        self.motion_asset_calls.append((motion_key, motion_path, loop))
        return True

    def start_motion_loop(self, motion_path: str, interval_ms: int = 300):
        self.motion_loop_calls.append((motion_path, interval_ms))

    def stop_motion_loop(self):
        pass

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        self.audio_calls.append((filename, title, update_status))
        return True

    def stop_music(self):
        return None


def run_wave_response_debug_probe() -> dict[str, object]:
    """wave_response 應走本地音檔路徑，並等待音訊與主動作都結束後再回 idle。"""
    directive = "[ACTION:wave_response] 嗨 你好嗎"
    _app = QCoreApplication.instance() or QCoreApplication([])
    with tempfile.TemporaryDirectory(prefix="echoes-debug-webm-") as temp_dir:
        wave_path = Path(temp_dir) / "Greeting.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        wave_path.write_bytes(b"debug")
        idle_path.write_bytes(b"debug")
        resolver = lambda motion_key: str({"wave_response": wave_path, "idle": idle_path}.get(motion_key, ""))
        window = _WaveProbeWindow()
        dispatcher = ActionDispatcher(
            window,
            library=object(),
            wave_worker_factory=lambda parent=None: _ImmediateWaveWorker(
                success=True,
                message="揮手問候音檔已完成",
                payload={"path": "wave.mp3", "title": "嗨 你好嗎"},
                parent=parent,
            ),
            motion_path_resolver=resolver,
            tts_enabled=False,
        )
        dispatcher._wave_greeting_audio_delay_ms = 80
        dispatched = dispatcher.dispatch(directive)
        audio_calls_before_delay = len(window.audio_calls)
        time.sleep(0.12)
        QCoreApplication.processEvents()
        audio_calls_after_delay = len(window.audio_calls)
        restore_before_audio_end = window.restore_idle_calls
        dispatcher._on_main_video_ended()
        restore_after_main_end = window.restore_idle_calls
        dispatcher._on_room_audio_ended()
        restore_after_audio_end = window.restore_idle_calls
        dispatcher._on_main_video_ended()
        idle_restored = window.restore_idle_calls >= 1
        dispatcher.shutdown()

        return {
            "directive": directive,
            "dispatched": dispatched,
            "status_calls": window.status_calls,
            "motion_calls": window.motion_calls,
            "motion_loop_calls": window.motion_loop_calls,
            "motion_asset_calls": window.motion_asset_calls,
            "audio_calls_before_delay": audio_calls_before_delay,
            "audio_calls_after_delay": audio_calls_after_delay,
            "restore_before_audio_end": restore_before_audio_end,
            "restore_after_main_end": restore_after_main_end,
            "restore_after_audio_end": restore_after_audio_end,
            "idle_restored": idle_restored,
            "restore_idle_calls": window.restore_idle_calls,
            "ok": (
                dispatched
                and bool(window.status_calls)
                and window.status_calls[0][0] == "嗨 你好嗎"
                and bool(window.motion_loop_calls)
                and window.motion_loop_calls[0][0].endswith("Greeting.webm")
                and not window.motion_asset_calls
                and audio_calls_before_delay == 0
                and audio_calls_after_delay == 1
                and restore_before_audio_end == 0
                and restore_after_main_end == 0
                and restore_after_audio_end == 0
                and idle_restored
                and window.restore_idle_calls == 1
            ),
        }

class WaveSensorTests(unittest.TestCase):
    def test_disabled_detection_skips_camera_capture(self):
        capture_requests: list[int] = []
        sensor = WaveSensor(
            config=WaveDetectionConfig(detection_enabled=False),
            capture_factory=lambda index: capture_requests.append(index),
        )
        sensor.run()
        self.assertEqual(capture_requests, [])

    def test_wave_sequence_detects_after_two_direction_changes(self):
        sensor = WaveSensor(
            config=WaveDetectionConfig(
                min_displacement_px=10,
                required_direction_changes=2,
                observation_window_seconds=2.0,
                cooldown_ms=1000,
            )
        )
        sequence = [
            (0.0, 100),
            (0.2, 135),
            (0.4, 105),
            (0.6, 145),
        ]
        detections = [
            sensor._register_horizontal_motion(center_x, timestamp)
            for timestamp, center_x in sequence
        ]
        self.assertEqual(detections, [False, False, False, True])

    def test_cooldown_blocks_repeated_triggers_until_interval_expires(self):
        sensor = WaveSensor(
            config=WaveDetectionConfig(
                min_displacement_px=10,
                required_direction_changes=2,
                observation_window_seconds=2.0,
                cooldown_ms=1000,
            )
        )
        sequence = [
            (0.0, 100),
            (0.2, 140),
            (0.4, 105),
            (0.6, 145),  # first trigger
            (0.7, 110),
            (0.8, 150),
            (0.9, 115),
            (1.0, 155),  # still in cooldown, must not trigger
            (1.8, 105),
            (2.0, 145),
            (2.2, 110),
            (2.4, 150),  # cooldown expired, can trigger again
        ]
        detections = [
            sensor._register_horizontal_motion(center_x, timestamp)
            for timestamp, center_x in sequence
        ]
        detection_indexes = [index for index, detected in enumerate(detections) if detected]
        self.assertEqual(detection_indexes[0], 3)
        self.assertFalse(any(detections[4:8]))
        self.assertGreaterEqual(len(detection_indexes), 2)
        self.assertGreater(detection_indexes[1], 7)

    def test_camera_unavailable_only_warns(self):
        warnings: list[str] = []
        capture = FakeCapture([], opened=False)
        sensor = WaveSensor(
            config=WaveDetectionConfig(detection_enabled=True),
            capture_factory=lambda _index: capture,
        )
        sensor.sensor_warning.connect(warnings.append)
        sensor.run()
        self.assertTrue(capture.released)
        self.assertEqual(len(warnings), 1)
        self.assertIn("攝影機無法開啟", warnings[0])

    @unittest.skipIf(cv2 is None, "OpenCV 尚未安裝於虛擬環境")
    def test_camera_flow_emits_wave_response_directive(self):
        frames = [object(), object(), object(), object()]
        capture = FakeCapture(frames)
        clock_points = iter([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        centers = iter([100, 140, 105, 145])
        directives: list[str] = []
        sensor = WaveSensor(
            config=WaveDetectionConfig(
                detection_enabled=True,
                min_contour_area=1000,
                min_displacement_px=18,
                required_direction_changes=2,
                observation_window_seconds=2.0,
                cooldown_ms=1200,
                loop_sleep_ms=0,
            ),
            capture_factory=lambda _index: capture,
            time_source=lambda: next(clock_points),
        )
        sensor._extract_motion_center_x = lambda _frame: next(centers)
        sensor.wave_detected.connect(lambda directive: directives.append(directive) or sensor.stop())
        sensor.run()
        self.assertTrue(capture.released)
        self.assertEqual(directives, [WAVE_RESPONSE_DIRECTIVE])

    @unittest.skipIf(cv2 is None, "OpenCV 尚未安裝於虛擬環境")
    def test_debug_window_flag_uses_preview_hook(self):
        frames = [object()]
        capture = FakeCapture(frames)
        preview_calls: list[tuple[int | None, float, bool]] = []
        sensor = WaveSensor(
            config=WaveDetectionConfig(
                detection_enabled=True,
                show_debug_window=True,
                loop_sleep_ms=0,
            ),
            capture_factory=lambda _index: capture,
            time_source=lambda: 0.5,
        )
        sensor._extract_motion_center_x = lambda _frame: None

        def preview_stub(_frame, center_x, timestamp, triggered):
            preview_calls.append((center_x, timestamp, triggered))
            sensor.stop()

        sensor._show_debug_window = preview_stub
        sensor.run()
        self.assertTrue(capture.released)
        self.assertEqual(preview_calls, [(None, 0.5, False)])


class WaveResponseIntegrationTests(unittest.TestCase):
    def test_main_wave_fast_path_adds_fixed_hi_text(self):
        self.assertEqual(build_wave_response_directive(WAVE_RESPONSE_DIRECTIVE), "[ACTION:wave_response] 嗨 你好嗎")

    def test_wave_response_action_dispatch_is_supported(self):
        result = run_wave_response_debug_probe()
        self.assertTrue(result["ok"], result)

    def test_wave_response_motion_resolves_to_running_forward_asset(self):
        library = CharacterLibrary()
        motion_path = library.get_action_motion_path(TEST_CHARACTER_ID, "wave_response")
        self.assertIsNotNone(motion_path)
        self.assertTrue(str(motion_path).endswith("Greeting.webm"))
        self.assertTrue(Path(motion_path).is_file())


if __name__ == "__main__":
    unittest.main()
