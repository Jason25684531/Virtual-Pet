from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from interaction_trace import InteractionLatencyTracker


@dataclass(frozen=True)
class PendingInteraction:
    source: str
    text: str


class InteractionTurnManager(QObject):
    """將 STT / Dev 輸入序列化成一輪一輪的完整互動。"""

    turn_started = pyqtSignal(str, str, str)  # trace_id, source, text
    turn_completed = pyqtSignal(str, str, str)  # trace_id, source, text
    queue_depth_changed = pyqtSignal(int)

    def __init__(self, brain_engine, latency_tracker: InteractionLatencyTracker, parent=None):
        super().__init__(parent)
        self._brain_engine = brain_engine
        self._latency_tracker = latency_tracker
        self._pending_inputs: deque[PendingInteraction] = deque()
        self._active_trace_id: str | None = None
        self._active_source = ""
        self._active_text = ""
        self._monitor = QTimer(self)
        self._monitor.setInterval(50)
        self._monitor.timeout.connect(self._on_monitor_tick)
        self._monitor.start()

    def submit(self, source: str, text: str) -> dict[str, object]:
        normalized_text = str(text or "").strip()
        normalized_source = str(source or "unknown").strip() or "unknown"
        if not normalized_text:
            return {
                "accepted": False,
                "started": False,
                "trace_id": None,
                "queue_position": 0,
            }

        self._pending_inputs.append(PendingInteraction(source=normalized_source, text=normalized_text))
        self.queue_depth_changed.emit(len(self._pending_inputs))
        started_trace_id = self._start_next_if_idle()
        return {
            "accepted": True,
            "started": bool(started_trace_id),
            "trace_id": started_trace_id,
            "queue_position": len(self._pending_inputs),
        }

    def pending_count(self) -> int:
        return len(self._pending_inputs)

    def has_active_turn(self) -> bool:
        return bool(self._active_trace_id)

    def shutdown(self):
        self._monitor.stop()
        self._pending_inputs.clear()
        self.queue_depth_changed.emit(0)

    def _start_next_if_idle(self) -> str | None:
        if self._active_trace_id or not self._pending_inputs:
            return None

        while self._pending_inputs:
            pending = self._pending_inputs.popleft()
            trace_id = self._latency_tracker.begin_interaction(pending.source, pending.text)
            if not self._brain_engine.send_to_brain(pending.text, trace_id=trace_id):
                self._latency_tracker.abort(trace_id, "interaction turn manager 未送入 BrainEngine")
                continue

            self._active_trace_id = trace_id
            self._active_source = pending.source
            self._active_text = pending.text
            self.turn_started.emit(trace_id, pending.source, pending.text)
            self.queue_depth_changed.emit(len(self._pending_inputs))
            return trace_id

        self.queue_depth_changed.emit(0)
        return None

    def _on_monitor_tick(self):
        if not self._active_trace_id:
            self._start_next_if_idle()
            return

        if self._latency_tracker.snapshot(self._active_trace_id) is not None:
            return

        finished_trace_id = self._active_trace_id
        finished_source = self._active_source
        finished_text = self._active_text
        self._active_trace_id = None
        self._active_source = ""
        self._active_text = ""
        self.turn_completed.emit(finished_trace_id, finished_source, finished_text)
        self.queue_depth_changed.emit(len(self._pending_inputs))
        self._start_next_if_idle()
