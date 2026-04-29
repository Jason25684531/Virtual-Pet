from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from interaction_trace import InteractionLatencyTracker
from sensors.stt_session_controller import STTSessionController


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeWorker:
    def __init__(self, parent=None):
        del parent
        self.recognized_text = _FakeSignal()
        self.warning_emitted = _FakeSignal()
        self.status_changed = _FakeSignal()
        self.listening_state_changed = _FakeSignal()
        self.finished = _FakeSignal()
        self._running = False
        self.stop_called = False
        self.quit_called = False
        self.wait_called = False

    def start(self):
        self._running = True
        self.status_changed.emit("Azure STT 已開始接收麥克風音訊。")
        self.listening_state_changed.emit(True)

    def stop(self):
        self.stop_called = True
        self._running = False
        self.listening_state_changed.emit(False)

    def quit(self):
        self.quit_called = True
        self.finished.emit()

    def wait(self, _timeout: int):
        self.wait_called = True
        return True

    def isRunning(self):
        return self._running

    def deleteLater(self):
        return None


class STTSessionControllerTests(unittest.TestCase):
    def test_start_and_stop_session_updates_state(self):
        controller = STTSessionController(worker_factory=_FakeWorker)
        states: list[bool] = []
        statuses: list[str] = []
        controller.session_state_changed.connect(states.append)
        controller.status_changed.connect(statuses.append)

        started = controller.start_session()
        stopped = controller.stop_session()

        self.assertTrue(started)
        self.assertTrue(stopped)
        self.assertEqual(states, [True, False])
        self.assertTrue(any("正在啟動 STT 收音" in message for message in statuses))
        self.assertTrue(any("正在停止 STT 收音" in message for message in statuses))


class InteractionLatencyTrackerTests(unittest.TestCase):
    def test_finalize_without_tts_prints_summary(self):
        tracker = InteractionLatencyTracker()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            trace_id = tracker.begin_interaction("test", "哈囉")
            tracker.mark_brain_queued(trace_id)
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")
            tracker.mark_brain_completed(trace_id)

        output = stdout.getvalue()
        self.assertIn("互動完成摘要", output)
        self.assertIn("bottleneck=", output)
        self.assertIsNone(tracker.snapshot(trace_id))

    def test_finalize_waits_for_tts_completion(self):
        tracker = InteractionLatencyTracker()
        trace_id = tracker.begin_interaction("test", "哈囉")
        tracker.mark_brain_queued(trace_id)
        tracker.mark_brain_started(trace_id)
        tracker.mark_fragment_emitted(trace_id, "好的。")
        tracker.mark_tts_enqueued(trace_id, "reply-1", "好的。")
        tracker.mark_brain_completed(trace_id)

        snapshot = tracker.snapshot(trace_id)
        self.assertIsNotNone(snapshot)
        self.assertFalse(snapshot["finalized"])

        tracker.mark_tts_stream_started(trace_id, "reply-1", 128)
        tracker.mark_tts_finished(trace_id, "reply-1", True, "完成")

        self.assertIsNone(tracker.snapshot(trace_id))


if __name__ == "__main__":
    unittest.main()
