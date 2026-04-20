"""
ECHOES — 攝影機揮手感測
以 OpenCV 在背景執行緒中讀取本機攝影機，偵測水平揮手後送出 action 指令。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Callable

from PyQt5.QtCore import QThread, pyqtSignal

try:
    import cv2
except ImportError:  # pragma: no cover - 依安裝環境決定
    cv2 = None


WAVE_RESPONSE_DIRECTIVE = "[ACTION:wave_response]"


@dataclass(frozen=True)
class WaveDetectionConfig:
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 360
    roi_top_ratio: float = 0.10
    roi_bottom_ratio: float = 0.82
    roi_left_ratio: float = 0.18
    roi_right_ratio: float = 0.82
    blur_kernel_size: int = 9
    threshold_value: int = 28
    dilation_iterations: int = 2
    min_contour_area: int = 1800
    min_displacement_px: int = 24
    required_direction_changes: int = 2
    observation_window_seconds: float = 1.4
    cooldown_ms: int = 2200
    loop_sleep_ms: int = 35


class WaveSensor(QThread):
    """以 QThread 執行攝影機輪詢與揮手判定。"""

    wave_detected = pyqtSignal(str)
    sensor_warning = pyqtSignal(str)

    def __init__(
        self,
        config: WaveDetectionConfig | None = None,
        capture_factory: Callable[[int], object] | None = None,
        time_source: Callable[[], float] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._config = config or WaveDetectionConfig()
        self._capture_factory = capture_factory or self._default_capture_factory
        self._time_source = time_source or time.monotonic
        self._stop_requested = False
        self._capture = None
        self._previous_roi_gray = None
        self._last_center_x: int | None = None
        self._direction_events: deque[tuple[float, int]] = deque()
        self._last_trigger_timestamp = float("-inf")
        self._warned_keys: set[str] = set()

    def stop(self):
        self._stop_requested = True

    def run(self):
        self._stop_requested = False
        self._reset_tracking_state()

        if cv2 is None:
            self._emit_warning_once("opencv-missing", "找不到 OpenCV，請先在虛擬環境內安裝 requirements.txt。")
            return

        capture = self._capture_factory(self._config.camera_index)
        self._capture = capture

        try:
            if capture is None or not getattr(capture, "isOpened", lambda: False)():
                self._emit_warning_once("camera-unavailable", "攝影機無法開啟，揮手感測已停用。")
                return

            self._configure_capture(capture)
            while not self._stop_requested:
                success, frame = capture.read()
                if not success or frame is None:
                    self._emit_warning_once("camera-read-failure", "攝影機影像讀取失敗，已保持安全待命。")
                    self.msleep(self._config.loop_sleep_ms)
                    continue

                self._warned_keys.discard("camera-read-failure")

                center_x = self._extract_motion_center_x(frame)
                timestamp = self._time_source()
                if self._register_horizontal_motion(center_x, timestamp):
                    self.wave_detected.emit(WAVE_RESPONSE_DIRECTIVE)

                self.msleep(self._config.loop_sleep_ms)
        finally:
            self._release_capture()

    @staticmethod
    def _default_capture_factory(camera_index: int):
        if cv2 is None:
            return None
        return cv2.VideoCapture(camera_index)

    def _configure_capture(self, capture):
        if hasattr(capture, "set"):
            capture.set(getattr(cv2, "CAP_PROP_FRAME_WIDTH", 3), self._config.frame_width)
            capture.set(getattr(cv2, "CAP_PROP_FRAME_HEIGHT", 4), self._config.frame_height)

    def _release_capture(self):
        capture = self._capture
        self._capture = None
        self._previous_roi_gray = None
        if capture is not None and hasattr(capture, "release"):
            capture.release()

    def _reset_tracking_state(self):
        self._previous_roi_gray = None
        self._last_center_x = None
        self._direction_events.clear()
        self._last_trigger_timestamp = float("-inf")

    def _emit_warning_once(self, key: str, message: str):
        if key in self._warned_keys:
            return
        self._warned_keys.add(key)
        self.sensor_warning.emit(message)

    def _extract_motion_center_x(self, frame) -> int | None:
        if cv2 is None or frame is None:
            return None

        resized = cv2.resize(frame, (self._config.frame_width, self._config.frame_height))
        roi = self._extract_roi(resized)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, self._blur_kernel_size(), 0)

        if self._previous_roi_gray is None:
            self._previous_roi_gray = blurred
            return None

        frame_delta = cv2.absdiff(self._previous_roi_gray, blurred)
        self._previous_roi_gray = blurred

        _, threshold = cv2.threshold(frame_delta, self._config.threshold_value, 255, cv2.THRESH_BINARY)
        threshold = cv2.dilate(threshold, None, iterations=self._config.dilation_iterations)
        contours = self._find_contours(threshold)
        if not contours:
            return None

        largest_contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest_contour) < self._config.min_contour_area:
            return None

        x, _, width, _ = cv2.boundingRect(largest_contour)
        return x + width // 2

    def _extract_roi(self, frame):
        frame_height, frame_width = frame.shape[:2]
        top = max(0, min(frame_height - 1, int(frame_height * self._config.roi_top_ratio)))
        bottom = max(top + 1, min(frame_height, int(frame_height * self._config.roi_bottom_ratio)))
        left = max(0, min(frame_width - 1, int(frame_width * self._config.roi_left_ratio)))
        right = max(left + 1, min(frame_width, int(frame_width * self._config.roi_right_ratio)))
        return frame[top:bottom, left:right]

    def _blur_kernel_size(self) -> tuple[int, int]:
        size = max(3, self._config.blur_kernel_size)
        if size % 2 == 0:
            size += 1
        return (size, size)

    @staticmethod
    def _find_contours(image) -> list[object]:
        contours_result = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if len(contours_result) == 2:
            contours, _ = contours_result
            return list(contours)
        _, contours, _ = contours_result
        return list(contours)

    def _register_horizontal_motion(self, center_x: int | None, timestamp: float) -> bool:
        self._prune_direction_events(timestamp)

        if center_x is None:
            self._last_center_x = None
            return False

        if self._last_center_x is None:
            self._last_center_x = center_x
            return False

        displacement = center_x - self._last_center_x
        self._last_center_x = center_x
        if abs(displacement) < self._config.min_displacement_px:
            return False

        direction = 1 if displacement > 0 else -1
        if self._is_in_cooldown(timestamp):
            self._direction_events.clear()
            return False

        self._remember_direction(direction, timestamp)

        required_events = self._config.required_direction_changes + 1
        if len(self._direction_events) < required_events:
            return False

        self._last_trigger_timestamp = timestamp
        self._direction_events.clear()
        self._last_center_x = None
        return True

    def _remember_direction(self, direction: int, timestamp: float):
        if self._direction_events and self._direction_events[-1][1] == direction:
            self._direction_events[-1] = (timestamp, direction)
            return
        self._direction_events.append((timestamp, direction))
        self._prune_direction_events(timestamp)

    def _prune_direction_events(self, timestamp: float):
        cutoff = timestamp - self._config.observation_window_seconds
        while self._direction_events and self._direction_events[0][0] < cutoff:
            self._direction_events.popleft()

    def _is_in_cooldown(self, timestamp: float) -> bool:
        return (timestamp - self._last_trigger_timestamp) * 1000 < self._config.cooldown_ms


def run_wave_sequence_probe(sequence: list[tuple[float, int | None]], config: WaveDetectionConfig | None = None) -> dict[str, object]:
    """用純座標序列快速驗證揮手演算法，不需啟動攝影機。"""

    sensor = WaveSensor(config=config)
    detections = []
    for timestamp, center_x in sequence:
        detections.append(sensor._register_horizontal_motion(center_x, timestamp))

    return {
        "sequence": sequence,
        "detections": detections,
        "trigger_count": sum(1 for detected in detections if detected),
        "ok": any(detections),
    }


def run_camera_unavailable_probe() -> dict[str, object]:
    """驗證攝影機開啟失敗時只發出警告、不拋出例外。"""

    class _ClosedCapture:
        def isOpened(self):
            return False

        def release(self):
            return None

    warnings: list[str] = []
    sensor = WaveSensor(capture_factory=lambda _index: _ClosedCapture())
    sensor.sensor_warning.connect(warnings.append)
    sensor.run()
    return {
        "warnings": warnings,
        "ok": bool(warnings) and "攝影機無法開啟" in warnings[0],
    }
