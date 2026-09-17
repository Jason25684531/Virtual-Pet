"""per-character-tts-model 3.4:tts_playback.py 直連 ElevenLabs factory 路徑的 model_id 傳遞。"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

from tts_playback import TtsPlaybackMixin


class _FakeCoordinator(TtsPlaybackMixin):
    """只餵 `_start_next_tts_worker` 需要的最小狀態,不牽動真正的 MotionCoordinator。"""

    def __init__(self, tts_worker_factory, character_id):
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


def _direct_elevenlabs_factory(captured: dict):
    def factory(text, reply_id, trace_id, voice_id, model_id=None, parent=None):
        captured.update(text=text, voice_id=voice_id, model_id=model_id)
        return MagicMock()

    return factory


def test_direct_elevenlabs_factory_receives_resolved_model_id():
    captured: dict = {}
    coordinator = _FakeCoordinator(_direct_elevenlabs_factory(captured), "char-Adol")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()

    assert captured["model_id"] == "eleven_v3"


def test_direct_elevenlabs_factory_uses_global_model_for_other_characters():
    captured: dict = {}
    coordinator = _FakeCoordinator(_direct_elevenlabs_factory(captured), "char-Jack")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()

    assert captured["model_id"] == "eleven_flash_v2_5"


def test_factory_without_model_id_param_is_not_passed_it():
    """既有的 signature 守門:factory 不吃 model_id 時不應該傳。"""
    captured: dict = {}

    def factory(text, reply_id, trace_id, voice_id, parent=None):
        captured.update(text=text, voice_id=voice_id)
        return MagicMock()

    coordinator = _FakeCoordinator(factory, "char-Adol")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()

    assert "model_id" not in captured
