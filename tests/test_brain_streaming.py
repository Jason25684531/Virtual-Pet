from __future__ import annotations

import sys
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api_client.brain_engine import BrainEngine
from interaction_trace import InteractionLatencyTracker


class _NoopLibrary:
    def get_current_character_id(self):
        return None

    def get_character(self, _character_id):
        return None


class _DummyMemory:
    def __init__(self):
        self.saved_contexts: list[tuple[dict[str, str], dict[str, str]]] = []

    def load_memory_variables(self, _inputs):
        return {"history": ""}

    def save_context(self, inputs, outputs):
        self.saved_contexts.append((inputs, outputs))


class _TestBrainEngine(BrainEngine):
    def __init__(self, streamed_tokens: list[str]):
        self._tracker = InteractionLatencyTracker()
        super().__init__(library=_NoopLibrary(), latency_tracker=self._tracker)
        self._streamed_tokens = list(streamed_tokens)
        self._dummy_memory = _DummyMemory()

    def _get_or_create_memory(self, _profile_id):
        return self._dummy_memory

    def _build_stream_prompt(self, **_kwargs):
        return "prompt"

    def _stream_llm_tokens(self, _profile, _prompt):
        for token in self._streamed_tokens:
            yield token


class BrainStreamingTests(unittest.TestCase):
    def test_handle_prompt_emits_action_before_sentence_chunks_and_saves_memory(self):
        engine = _TestBrainEngine(
            [
                "[ACTION:listen]哈囉，",
                "今天天氣很好。",
                "一起加油",
            ]
        )
        emitted: list[str] = []
        traced_fragments: list[tuple[str, str | None]] = []
        warnings: list[str] = []
        engine.message_received.connect(emitted.append)
        engine.streamed_fragment.connect(lambda fragment, trace_id: traced_fragments.append((fragment, trace_id)))
        engine.warning_emitted.connect(warnings.append)

        trace_id = engine._tracker.begin_interaction("test", "測試輸入")
        engine._handle_prompt("測試輸入", trace_id=trace_id)

        self.assertEqual(
            emitted,
            ["[ACTION:listen]", "哈囉，", "今天天氣很好。", "一起加油"],
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            traced_fragments,
            [
                ("[ACTION:listen]", trace_id),
                ("哈囉，", trace_id),
                ("今天天氣很好。", trace_id),
                ("一起加油", trace_id),
            ],
        )
        self.assertEqual(len(engine._dummy_memory.saved_contexts), 1)
        saved_inputs, saved_outputs = engine._dummy_memory.saved_contexts[0]
        self.assertEqual(saved_inputs, {"input": "測試輸入"})
        self.assertEqual(
            saved_outputs,
            {"response": "[ACTION:listen] 哈囉，今天天氣很好。一起加油"},
        )
        self.assertIsNone(engine._tracker.snapshot(trace_id))


if __name__ == "__main__":
    unittest.main()
