"""reduce-turn-latency 2.x:不可發聲文字不送 TTS,聊天顯示不受影響。"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

from PyQt5.QtCore import QObject

from action_dispatcher import MotionCoordinator
from tts_playback import TtsPlaybackMixin, _speakable


class _Coordinator(TtsPlaybackMixin, QObject):
    def __init__(self):
        super().__init__()
        self._tts_enabled = True
        self._tts_worker_factory = lambda *_a, **_k: None
        self._suppressed_traces = {}
        self._pending_tts_chunks = queue.Queue()
        self._reply_texts = {}
        self._trace_pending_tts_counts = {}
        self._pending_actions = {}
        self._current_loop_action_key = None
        self._latency_tracker = None
        self._window = MagicMock()
        self._start_next_tts_worker = lambda: None

    def speak_text(self, message, trace_id=None, has_action=False):
        self._synthesize_tts(message, tone="neutral", trace_id=trace_id)


def test_speakable_strips_emoji_and_keeps_text_and_digits():
    assert _speakable("😊") == ""
    assert _speakable("🎉 ！") == ""
    assert _speakable("汪汪！🐶 你覺得好笑嗎？") == "汪汪！ 你覺得好笑嗎？"
    assert _speakable("2027。") == "2027。"
    assert _speakable("👨‍👩‍👧 hi") == "hi"  # ZWJ 序列整串移除


def test_emoji_only_chunk_creates_no_tts_request_but_still_displays():
    c = _Coordinator()
    MotionCoordinator._enqueue_stream_chunk(c, "😊", "trace-1")
    c._window.append_conversation_assistant.assert_called_once_with("trace-1", "😊")
    assert c._pending_tts_chunks.empty()


def test_mixed_chunk_displays_original_but_speaks_without_emoji():
    c = _Coordinator()
    MotionCoordinator._enqueue_stream_chunk(c, "汪汪！🐶 你覺得好笑嗎？", "trace-1")
    c._window.append_conversation_assistant.assert_called_once_with("trace-1", "汪汪！🐶 你覺得好笑嗎？")
    assert c._pending_tts_chunks.get_nowait()[1] == "汪汪！ 你覺得好笑嗎？"


def test_speakable_drops_news_list_numbers_but_keeps_other_digits():
    assert _speakable("1. 薩爾達推出新樂高") == "薩爾達推出新樂高"
    assert _speakable("好的！2. Denzel's Training Day 免費了") == "好的！Denzel's Training Day 免費了"
    assert _speakable("1.") == ""
    assert _speakable("花了 3.5 小時，共 2027 筆") == "花了 3.5 小時，共 2027 筆"
