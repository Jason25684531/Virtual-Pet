from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from action_dispatcher import run_wave_response_debug_probe
from character_library import CharacterLibrary
from sensors.camera_vision import (
    WAVE_RESPONSE_DIRECTIVE,
    WaveDetectionConfig,
    WaveSensor,
    cv2,
)


TEST_CHARACTER_ID = "20260415_168888_初音"


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
        sensor = WaveSensor(capture_factory=lambda _index: capture)
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
    def test_wave_response_action_dispatch_is_supported(self):
        result = run_wave_response_debug_probe()
        self.assertTrue(result["ok"], result)

    def test_wave_response_motion_resolves_to_running_forward_asset(self):
        library = CharacterLibrary()
        motion_path = library.get_action_motion_path(TEST_CHARACTER_ID, "wave_response")
        self.assertIsNotNone(motion_path)
        self.assertTrue(str(motion_path).endswith("running_forward.webm"))
        self.assertTrue(Path(motion_path).is_file())


if __name__ == "__main__":
    unittest.main()
