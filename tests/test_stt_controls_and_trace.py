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
        self.speech_started = _FakeSignal()
        self.speech_ended = _FakeSignal()
        self.recognized_text = _FakeSignal()
        self.recognizing_text = _FakeSignal()
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

    def test_partial_preview_is_forwarded_without_final_submission(self):
        controller = STTSessionController(worker_factory=_FakeWorker)
        partials: list[str] = []
        finals: list[str] = []
        controller.recognizing_text.connect(partials.append)
        controller.recognized_text.connect(finals.append)

        controller.start_session()
        worker = controller._worker
        worker.recognizing_text.emit("半句")

        self.assertEqual(partials, ["半句"])
        self.assertEqual(finals, [])

    def test_controller_preserves_trace_id_from_stt_events_to_finalized_text(self):
        tracker = InteractionLatencyTracker()
        controller = STTSessionController(worker_factory=_FakeWorker, latency_tracker=tracker)
        finalized: list[tuple[str, str | None]] = []
        controller.recognized_result.connect(lambda text, trace_id: finalized.append((text, trace_id)))

        controller.start_session()
        worker = controller._worker
        worker.speech_started.emit()
        worker.speech_ended.emit()
        worker.recognized_text.emit("哈囉")

        self.assertEqual(len(finalized), 1)
        text, trace_id = finalized[0]
        self.assertEqual(text, "哈囉")
        self.assertTrue(trace_id)
        snapshot = tracker.snapshot(trace_id)
        self.assertIsNotNone(snapshot)
        self.assertIn("stt_speech_started", snapshot["stages"])
        self.assertIn("stt_speech_ended", snapshot["stages"])
        self.assertIn("stt_finalized", snapshot["stages"])


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
        tracker.mark_tts_playback_started(trace_id, "reply-1")
        tracker.mark_tts_finished(trace_id, "reply-1", True, "完成")

        self.assertIsNone(tracker.snapshot(trace_id))

    def test_completed_trace_exposes_stt_tail_and_eos_metrics(self):
        tracker = InteractionLatencyTracker()
        trace_id = tracker.begin_interaction("stt", "哈囉")
        tracker.mark_stt_speech_started(trace_id)
        tracker.mark_stt_speech_ended(trace_id)
        tracker.mark_stt_finalized(trace_id, "哈囉")
        tracker.mark_brain_queued(trace_id)
        tracker.mark_brain_started(trace_id)
        tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")
        tracker.mark_action_dispatched(trace_id, "listen")
        tracker.mark_tts_enqueued(trace_id, "reply-1", "哈囉。")
        tracker.mark_tts_stream_started(trace_id, "reply-1", 128)
        tracker.mark_tts_playback_started(trace_id, "reply-1")
        tracker.mark_brain_completed(trace_id)
        tracker.mark_tts_finished(trace_id, "reply-1", True, "完成")

        completed = tracker.get_completed_trace(trace_id)
        self.assertIsNotNone(completed)
        self.assertIn("stt_tail", completed["stage_durations"])
        self.assertIn("eos_to_first_action", completed["stage_durations"])
        self.assertIn("eos_to_first_audio", completed["stage_durations"])
        self.assertIn("eos_to_complete", completed["stage_durations"])

    def test_tts_expected_blocks_finalize_until_downstream_tts_finishes(self):
        tracker = InteractionLatencyTracker()
        trace_id = tracker.begin_interaction("test", "哈囉")
        tracker.mark_brain_queued(trace_id)
        tracker.mark_brain_started(trace_id)
        tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")
        tracker.mark_tts_expected(trace_id, "哈囉。")
        tracker.mark_brain_completed(trace_id)

        snapshot = tracker.snapshot(trace_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["tts_expected"], 1)
        self.assertEqual(snapshot["tts_enqueued"], 0)

        tracker.mark_tts_enqueued(trace_id, "reply-1", "哈囉。")
        tracker.mark_tts_stream_started(trace_id, "reply-1", 128)
        tracker.mark_tts_playback_started(trace_id, "reply-1")
        tracker.mark_tts_finished(trace_id, "reply-1", True, "完成")

        self.assertIsNone(tracker.snapshot(trace_id))


if __name__ == "__main__":
    unittest.main()
