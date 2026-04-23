from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PyQt5.QtCore import QUrl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from action_dispatcher import (
    ActionDispatcher,
    _ImmediateTTSWorker,
    _ImmediateServiceWorker,
    run_streamed_action_first_debug_probe,
    run_tts_dispatch_debug_probe,
)
from api_client.brain_engine import BrainEngine, StreamedReplyParser
from interaction_trace import InteractionLatencyTracker


class _NoopLibrary:
    def get_current_character_id(self):
        return None


class _DispatchProbeWindow:
    DEMO_MOTION_MAPPING = {
        "idle": "Idle.webm",
        "report_news": "report_news.webm",
        "wave_response": "running_forward.webm",
    }

    def __init__(self, demo_dir: str):
        self.DEMO_ANIMATIONS_DIR = demo_dir
        self.status_calls: list[tuple[str, str, int]] = []
        self.played_assets: list[tuple[str, str, bool]] = []
        self.restore_idle_calls = 0

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.played_assets.append((motion_key, motion_path, loop))
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        del filename, title, update_status
        return True

    def stop_music(self):
        return None


class _FakePage:
    def __init__(self):
        self.scripts: list[str] = []

    def runJavaScript(self, script: str):
        self.scripts.append(script)


class _FakeWebView:
    def __init__(self):
        self._page = _FakePage()

    def page(self):
        return self._page


class _ChangeVideoHarness:
    RAW_JAVASCRIPT_MARKER = "__raw_javascript__"

    def __init__(self):
        self._webview_ready = True
        self._pending_javascript_calls: list[tuple[str, tuple[object, ...]]] = []
        self.web_view = _FakeWebView()

    def change_video(self, filename, loop=True) -> bool:
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.exists(absolute_path):
            print(f"[ECHOES ERROR] WebM 檔案不存在: {absolute_path or filename}")
            return False

        source_url = QUrl.fromLocalFile(absolute_path).toString(QUrl.FullyEncoded)
        print(f"[ECHOES] 送出影片 URL: {source_url}")
        if loop:
            self._run_javascript("setIdleVideo", source_url)
            return True

        safe_url = self._escape_javascript_single_quoted_string(source_url)
        self._run_raw_javascript(
            "if (window.playTemporaryVideo) { "
            f"window.playTemporaryVideo('{safe_url}');"
            " } else { console.error('[ECHOES] playTemporaryVideo bridge 不存在'); }"
        )
        return True

    def _run_raw_javascript(self, script: str):
        if not self._webview_ready:
            self._pending_javascript_calls.append((self.RAW_JAVASCRIPT_MARKER, (script,)))
            return

        self.web_view.page().runJavaScript(script)

    def _run_javascript(self, function_name: str, *args):
        if not self._webview_ready:
            self._pending_javascript_calls.append((function_name, args))
            return

        if function_name == self.RAW_JAVASCRIPT_MARKER:
            script = str(args[0]) if args else ""
            self.web_view.page().runJavaScript(script)
            return

        self.web_view.page().runJavaScript(self._build_javascript_bridge_call(function_name, *args))

    @staticmethod
    def _build_javascript_bridge_call(function_name: str, *args) -> str:
        js_function_name = json.dumps(function_name)
        js_args = ", ".join(json.dumps(arg) for arg in args)
        return (
            "(function(){"
            f"var fn = window[{js_function_name}];"
            f"if (typeof fn !== 'function') {{ console.warn('[ECHOES] JS bridge 缺少函式:', {js_function_name}); return false; }}"
            f"fn({js_args});"
            "return true;"
            "})();"
        )

    @staticmethod
    def _escape_javascript_single_quoted_string(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )

    def _resolve_media_path(self, filename: str) -> str | None:
        return os.path.abspath(os.path.normpath(filename))


class ActionPlaybackTests(unittest.TestCase):
    def test_missing_motion_falls_back_to_idle_with_warning(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-fallback-") as temp_dir:
            idle_path = Path(temp_dir) / "Idle.webm"
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(window, library=_NoopLibrary(), tts_enabled=False)
            stdout_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer):
                dispatched = dispatcher.dispatch("[ACTION:wave_response]")

            self.assertTrue(dispatched)
            self.assertEqual(len(window.played_assets), 1)
            motion_key, played_path, loop = window.played_assets[0]
            self.assertEqual(motion_key, "wave_response")
            self.assertEqual(played_path, os.path.abspath(str(idle_path)))
            self.assertTrue(loop)
            self.assertEqual(window.restore_idle_calls, 0)
            self.assertIn("[ECHOES WARNING] 找不到動作檔案: wave_response, 退回 Idle", stdout_buffer.getvalue())

    def test_tts_dispatch_keeps_motion_before_audio(self):
        result = run_tts_dispatch_debug_probe()
        self.assertTrue(result["ok"], result)

    def test_brain_engine_normalizes_alias_action_to_supported_action(self):
        normalized = BrainEngine._normalize_reply("我來幫你看今天重點。 [ACTION:news]")
        self.assertEqual(normalized, "[ACTION:report_news] 我來幫你看今天重點。")

    def test_streamed_reply_parser_emits_action_then_sentence_chunks(self):
        parser = StreamedReplyParser()

        outputs = []
        outputs.extend(parser.feed("[ACTION:listen]哈囉，"))
        outputs.extend(parser.feed("今天一起加油。"))
        outputs.extend(parser.flush())

        self.assertEqual(outputs, ["[ACTION:listen]", "哈囉，", "今天一起加油。"])

    def test_streamed_reply_parser_flushes_trailing_text_without_punctuation(self):
        parser = StreamedReplyParser()

        outputs = []
        outputs.extend(parser.feed("這是一段"))
        outputs.extend(parser.feed("還沒結尾"))
        outputs.extend(parser.flush())

        self.assertEqual(outputs, ["這是一段還沒結尾"])

    def test_dispatcher_accepts_alias_action_name(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-alias-") as temp_dir:
            report_news_path = Path(temp_dir) / "report_news.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            report_news_path.write_bytes(b"news")
            idle_path.write_bytes(b"idle")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                news_worker_factory=lambda parent=None: _ImmediateServiceWorker(
                    success=True,
                    message="新聞已完成",
                    payload={"headline": "測試頭條"},
                    parent=parent,
                ),
                tts_enabled=False,
            )
            stdout_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer):
                dispatched = dispatcher.dispatch("[ACTION:news] 今天幫你整理頭條")

            self.assertTrue(dispatched)
            self.assertEqual(window.played_assets[0][0], "report_news")
            self.assertEqual(window.played_assets[0][1], os.path.abspath(str(report_news_path)))
            self.assertIn("action alias `news` 已正規化為 `report_news`", stdout_buffer.getvalue())

    def test_change_video_uses_encoded_file_url_and_direct_js_for_temporary_motion(self):
        with tempfile.TemporaryDirectory(prefix="echoes-change-video-") as temp_dir:
            webm_path = Path(temp_dir) / "初音 demo's motion.webm"
            webm_path.write_bytes(b"webm")

            harness = _ChangeVideoHarness()
            changed = harness.change_video(str(webm_path), loop=False)

            self.assertTrue(changed)
            expected_url = QUrl.fromLocalFile(os.path.abspath(str(webm_path))).toString(QUrl.FullyEncoded)
            expected_script = (
                "if (window.playTemporaryVideo) { "
                "window.playTemporaryVideo('"
                f"{_ChangeVideoHarness._escape_javascript_single_quoted_string(expected_url)}"
                "');"
                " } else { console.error('[ECHOES] playTemporaryVideo bridge 不存在'); }"
            )
            self.assertEqual(harness.web_view.page().scripts, [expected_script])

    def test_streamed_action_prefix_starts_motion_before_text_chunk(self):
        result = run_streamed_action_first_debug_probe()
        self.assertTrue(result["ok"], result)

    def test_dispatcher_completes_correlated_trace_after_immediate_tts(self):
        with tempfile.TemporaryDirectory(prefix="echoes-action-trace-") as temp_dir:
            listen_path = Path(temp_dir) / "listen.webm"
            idle_path = Path(temp_dir) / "Idle.webm"
            listen_path.write_bytes(b"listen")
            idle_path.write_bytes(b"idle")

            tracker = InteractionLatencyTracker()
            trace_id = tracker.begin_interaction("test", "你好")
            tracker.mark_brain_queued(trace_id)
            tracker.mark_brain_started(trace_id)
            tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")

            window = _DispatchProbeWindow(temp_dir)
            dispatcher = ActionDispatcher(
                window,
                library=_NoopLibrary(),
                motion_path_resolver=lambda motion_key: str(
                    {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
                ),
                tts_worker_factory=_ImmediateTTSWorker,
                latency_tracker=tracker,
            )

            dispatched = dispatcher.dispatch("[ACTION:listen] 你好。", trace_id=trace_id)
            tracker.mark_brain_completed(trace_id)

            self.assertTrue(dispatched)
            self.assertEqual(window.played_assets[0][0], "listen")
            self.assertIsNone(tracker.snapshot(trace_id))


if __name__ == "__main__":
    unittest.main()
