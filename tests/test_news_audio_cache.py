from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import action_services

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from action_services import (
    FIXED_NEWS_SCRIPT,
    NewsFetchWorker,
    WAVE_GREETING_SCRIPT,
    WaveGreetingWorker,
    build_fixed_intent_payload,
    generate_fixed_intent_text,
)


class NewsAudioCacheTests(unittest.TestCase):
    def test_first_run_generates_then_second_run_uses_cached_audio(self):
        with tempfile.TemporaryDirectory(prefix="echoes-news-cache-") as temp_dir:
            synth_calls: list[str] = []
            results: list[tuple[bool, str, object]] = []

            def synthesizer(script: str, _voice_config: dict, _cache_path: Path):
                synth_calls.append(script)
                return b"fake mp3 bytes"

            worker = NewsFetchWorker(character_id="miku", cache_dir=temp_dir, synthesizer=synthesizer)
            worker.finished_signal.connect(lambda success, message, payload: results.append((success, message, payload)))
            worker.run()

            worker_cached = NewsFetchWorker(character_id="miku", cache_dir=temp_dir, synthesizer=synthesizer)
            worker_cached.finished_signal.connect(lambda success, message, payload: results.append((success, message, payload)))
            worker_cached.run()

            self.assertEqual(synth_calls, [FIXED_NEWS_SCRIPT])
            self.assertEqual(len(results), 2)
            self.assertTrue(results[0][0])
            self.assertTrue(results[1][0])
            self.assertFalse(results[0][2]["cached"])
            self.assertTrue(results[1][2]["cached"])
            self.assertEqual(results[0][2]["path"], results[1][2]["path"])
            self.assertTrue(Path(results[1][2]["path"]).is_file())

    def test_news_worker_does_not_fetch_rss(self):
        with tempfile.TemporaryDirectory(prefix="echoes-news-no-rss-") as temp_dir:
            worker = NewsFetchWorker(
                character_id="miku",
                cache_dir=temp_dir,
                synthesizer=lambda *_args: b"fake mp3 bytes",
            )
            results: list[tuple[bool, str, object]] = []
            worker.finished_signal.connect(lambda success, message, payload: results.append((success, message, payload)))

            with patch("action_services.requests.get", side_effect=AssertionError("RSS should not be used")):
                worker.run()

            self.assertTrue(results[0][0])
            self.assertEqual(results[0][2]["script"], FIXED_NEWS_SCRIPT)

    def test_synthesis_failure_reports_failure_without_cache_file(self):
        with tempfile.TemporaryDirectory(prefix="echoes-news-fail-") as temp_dir:
            worker = NewsFetchWorker(
                character_id="miku",
                cache_dir=temp_dir,
                synthesizer=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            results: list[tuple[bool, str, object]] = []
            worker.finished_signal.connect(lambda success, message, payload: results.append((success, message, payload)))

            worker.run()

            self.assertFalse(results[0][0])
            self.assertIn("固定新聞音檔準備失敗", results[0][1])
            self.assertEqual(list(Path(temp_dir).glob("*.mp3")), [])

    def test_wave_greeting_worker_uses_cached_audio_on_second_run(self):
        with tempfile.TemporaryDirectory(prefix="echoes-wave-cache-") as temp_dir:
            synth_calls: list[str] = []
            results: list[tuple[bool, str, object]] = []

            def synthesizer(script: str, _voice_config: dict, _cache_path: Path):
                synth_calls.append(script)
                return b"fake mp3 bytes"

            worker = WaveGreetingWorker(character_id="miku", cache_dir=temp_dir, synthesizer=synthesizer)
            worker.finished_signal.connect(lambda success, message, payload: results.append((success, message, payload)))
            worker.run()

            worker_cached = WaveGreetingWorker(character_id="miku", cache_dir=temp_dir, synthesizer=synthesizer)
            worker_cached.finished_signal.connect(lambda success, message, payload: results.append((success, message, payload)))
            worker_cached.run()

            self.assertEqual(synth_calls, [WAVE_GREETING_SCRIPT])
            self.assertEqual(len(results), 2)
            self.assertTrue(results[0][0])
            self.assertTrue(results[1][0])
            self.assertFalse(results[0][2]["cached"])
            self.assertTrue(results[1][2]["cached"])
            self.assertEqual(results[0][2]["text"], WAVE_GREETING_SCRIPT)
            self.assertEqual(results[0][2]["title"], WAVE_GREETING_SCRIPT)
            self.assertEqual(results[0][2]["path"], results[1][2]["path"])
            self.assertTrue(Path(results[1][2]["path"]).is_file())

    def test_fixed_intent_payload_reuses_cache_and_separates_characters(self):
        with tempfile.TemporaryDirectory(prefix="echoes-fixed-intent-cache-") as temp_dir:
            synth_calls: list[str] = []

            def synthesizer(script: str, _voice_config: dict, _cache_path: Path):
                synth_calls.append(script)
                return b"fake mp3 bytes"

            miku_first = build_fixed_intent_payload(
                "joke",
                "miku",
                cache_dir=temp_dir,
                synthesizer=synthesizer,
                text_generator=lambda: "這是 miku 的笑話。",
            )
            miku_cached = build_fixed_intent_payload(
                "joke",
                "miku",
                cache_dir=temp_dir,
                synthesizer=synthesizer,
                text_generator=lambda: (_ for _ in ()).throw(RuntimeError("should not regenerate")),
            )
            choppr_first = build_fixed_intent_payload(
                "joke",
                "Choppr",
                cache_dir=temp_dir,
                synthesizer=synthesizer,
                text_generator=lambda: "這是 Choppr 的笑話。",
            )

            self.assertEqual(
                synth_calls,
                ["這是 miku 的笑話。", "這是 Choppr 的笑話。"],
            )
            self.assertFalse(miku_first["cached"])
            self.assertTrue(miku_cached["cached"])
            self.assertFalse(choppr_first["cached"])
            self.assertEqual(miku_first["path"], miku_cached["path"])
            self.assertNotEqual(miku_first["path"], choppr_first["path"])
            self.assertEqual(miku_first["action_name"], "laugh")
            self.assertEqual(miku_first["text"], "這是 miku 的笑話。")

    def test_share_fixed_intent_generation_uses_supportive_request_text(self):
        captured_messages: list[object] = []

        class _FakeResponse:
            content = "當然可以，我在這裡聽你說。"

        class _FakeLLM:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return _FakeResponse()

        with patch.object(action_services.config, "OPENAI_API_KEY", "test-key"):
            text = generate_fixed_intent_text(
                "share",
                "miku",
                llm_factory=lambda **_kwargs: _FakeLLM(),
            )

        human_message = captured_messages[-1]
        if isinstance(human_message, dict):
            human_content = human_message.get("content")
        else:
            human_content = getattr(human_message, "content", "")
        self.assertIn("我今天心情不好 可以聽我分享嘛", str(human_content))
        self.assertIn("明確表達你願意傾聽與陪伴", str(human_content))
        self.assertEqual(text, "當然可以，我在這裡聽你說。")


if __name__ == "__main__":
    unittest.main()
