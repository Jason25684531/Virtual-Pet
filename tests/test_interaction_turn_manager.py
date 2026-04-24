from __future__ import annotations

import sys
from pathlib import Path
import unittest

from PyQt5.QtCore import QCoreApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from interaction_trace import InteractionLatencyTracker
from interaction_turn_manager import InteractionTurnManager


class _FakeBrainEngine:
    def __init__(self):
        self.sent_items: list[tuple[str, str]] = []

    def send_to_brain(self, text: str, profile=None, trace_id: str | None = None):
        del profile
        if not text or not trace_id:
            return False
        self.sent_items.append((text, trace_id))
        return True


class InteractionTurnManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def test_serializes_turns_until_previous_trace_finishes(self):
        tracker = InteractionLatencyTracker()
        brain = _FakeBrainEngine()
        manager = InteractionTurnManager(brain, tracker)
        started: list[tuple[str, str, str]] = []
        completed: list[str] = []
        manager.turn_started.connect(lambda trace_id, source, text: started.append((trace_id, source, text)))
        manager.turn_completed.connect(lambda trace_id, _source, _text: completed.append(trace_id))

        first = manager.submit("stt", "第一句")
        second = manager.submit("stt", "第二句")

        self.assertTrue(first["accepted"])
        self.assertTrue(first["started"])
        self.assertTrue(second["accepted"])
        self.assertFalse(second["started"])
        self.assertEqual(second["queue_position"], 1)
        self.assertEqual(len(brain.sent_items), 1)
        first_trace_id = started[0][0]

        tracker.mark_brain_completed(first_trace_id)
        manager._on_monitor_tick()

        self.assertEqual(completed, [first_trace_id])
        self.assertEqual(len(brain.sent_items), 2)
        self.assertEqual(brain.sent_items[1][0], "第二句")
        self.assertEqual(len(started), 2)
        self.assertEqual(started[1][2], "第二句")
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
