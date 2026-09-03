from __future__ import annotations

import random
from collections import deque
from collections.abc import Callable

from PyQt5.QtCore import QTimer


class ProactiveGreeter:
    def __init__(
        self,
        speak: Callable[[str], None],
        is_busy: Callable[[], bool],
        phrases: list[str] | tuple[str, ...],
        interval_sec: float,
    ) -> None:
        self._speak = speak
        self._is_busy = is_busy
        self._phrases = tuple(str(phrase).strip() for phrase in phrases if str(phrase).strip())
        self._history: deque[str] = deque(maxlen=2)
        self._timer = QTimer()
        self._timer.setInterval(max(1, round(float(interval_sec) * 1000)))
        self._timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def reset(self) -> None:
        self.stop()
        self._history.clear()
        self.start()

    def _on_tick(self) -> None:
        if not self._phrases or self._is_busy():
            return
        available = [phrase for phrase in self._phrases if phrase not in self._history]
        phrase = random.choice(available or list(self._phrases))
        self._history.append(phrase)
        self._speak(phrase)
