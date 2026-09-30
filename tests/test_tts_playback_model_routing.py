"""per-character-tts-model 3.4 / unify-tts-provider-contract:
tts_playback._start_next_tts_worker 建構的 TtsRequest 攜帶已解析完成的 model_id。

改寫前這裡測的是「factory 簽名不吃 model_id 時不傳」的 inspect.signature 探測
行為;契約統一後不再有選擇性傳參——TtsRequest 一律攜帶已解析的 model_id,factory
要不要用是它自己的事,不需要 tts_playback 猜。"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

from PyQt5.QtCore import QObject

from api_client.tts_contract import TtsRequest
from tts_playback import TtsPlaybackMixin


class _FakeCoordinator(TtsPlaybackMixin, QObject):
    """只餵 `_start_next_tts_worker` 需要的最小狀態,不牽動真正的 MotionCoordinator。"""

    def __init__(self, tts_worker_factory, character_id):
        super().__init__()
        self._tts_worker_factory = tts_worker_factory
        self._character_id = character_id
        self._active_tts_worker = None
        self._pending_tts_chunks = queue.Queue()
        self._suppressed_traces = set()
        self._tts_not_expected_traces = set()
        self._trace_tts_providers = {}
        self._can_start_trace_audio = lambda *_a, **_k: True
        self._audio_worker = MagicMock()
        self._workers = []

    def _current_character_id(self):
        return self._character_id

    def _on_tts_finished(self, *_a, **_k):
        pass

    def _on_tts_progress(self, *_a, **_k):
        pass

    def _record_spoken_reply(self, *_a, **_k):
        pass

    def _finish_loop_action_if_tts_idle(self):
        pass


def _capturing_factory(captured: dict):
    def factory(request: TtsRequest, parent=None):
        captured["request"] = request
        return MagicMock()

    return factory


def test_direct_factory_receives_resolved_model_id_for_dedicated_character():
    captured: dict = {}
    coordinator = _FakeCoordinator(_capturing_factory(captured), "char-Adol")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()

    assert captured["request"].model_id == "eleven_v3"


def test_direct_factory_uses_global_model_for_other_characters():
    captured: dict = {}
    coordinator = _FakeCoordinator(_capturing_factory(captured), "char-Jack")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()

    assert captured["request"].model_id == "eleven_flash_v2_5"


def test_request_text_and_reply_id_flow_through_unchanged():
    captured: dict = {}
    coordinator = _FakeCoordinator(_capturing_factory(captured), "char-Adol")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()

    request = captured["request"]
    assert request.text == "hello"
    assert request.reply_id == "reply-1"
    assert request.trace_id == "trace-1"
