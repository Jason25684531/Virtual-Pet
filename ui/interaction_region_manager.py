from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect


class InteractionRegionManager:
    """Hold the WebView's CSS-pixel interaction rectangles."""

    def __init__(self) -> None:
        self._regions: list[QRect] = []
        self._fallback_interactive = True

    def update_regions(self, rects: list[QRect]) -> None:
        self._regions = [rect for rect in rects if isinstance(rect, QRect) and not rect.isEmpty()]

    def clear(self) -> None:
        self._regions.clear()

    def hit_test(self, local_pos: QPoint) -> bool:
        return self._fallback_interactive if not self._regions else any(rect.contains(local_pos) for rect in self._regions)

    def set_fallback_interactive(self, interactive: bool) -> None:
        self._fallback_interactive = bool(interactive)

    @property
    def region_count(self) -> int:
        return len(self._regions)

    @staticmethod
    def native_to_css(value: int, device_pixel_ratio: float) -> int:
        """Convert a native Windows coordinate to Qt/WebEngine CSS pixels."""
        return round(value / device_pixel_ratio)
