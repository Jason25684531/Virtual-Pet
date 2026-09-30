"""unify-tts-provider-contract:TtsRequest/TtsProvider 契約與備援鏈組裝、
以及 tts_playback 的訊號接線是否與 StreamingTTSWorker 契約相容。"""

from __future__ import annotations

import dataclasses
import queue

import pytest
from PyQt5.QtCore import QObject, pyqtSignal
from unittest.mock import MagicMock

import config
from api_client.tts_contract import (
    STREAMING_TTS_WORKER_MEMBERS,
    TtsProvider,
    TtsRequest,
    build_tts_provider_chain,
)
from tts_playback import TtsPlaybackMixin


# --------------------------------------------------------------------------
# 2.1 TtsRequest:frozen、欄位齊備
# --------------------------------------------------------------------------

def test_tts_request_is_frozen():
    request = TtsRequest(
        text="hi", reply_id="r1", trace_id="t1", character_id="char-Adol",
        voice_id="voice-1", model_id="eleven_v3",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.text = "changed"


def test_tts_request_has_all_contract_fields():
    fields = {f.name for f in dataclasses.fields(TtsRequest)}
    assert fields == {
        "text", "reply_id", "trace_id", "character_id", "voice_id", "model_id",
        "preferred_provider", "resolved_tts_mode", "pcm_stream_sink", "playback_guard",
    }


def test_tts_request_optional_fields_default_empty():
    request = TtsRequest(
        text="hi", reply_id="r1", trace_id="t1", character_id="char-Jack",
        voice_id="voice-1", model_id="eleven_flash_v2_5",
    )
    assert request.preferred_provider == ""
    assert request.resolved_tts_mode == ""
    assert request.pcm_stream_sink is None
    assert request.playback_guard is None


# --------------------------------------------------------------------------
# 2.2 build_tts_provider_chain:角色/偏好決定順序
# --------------------------------------------------------------------------

def test_dedicated_voice_character_gets_elevenlabs_first():
    assert "char-Adol" in config.BUILTIN_CHARACTER_ELEVENLABS_VOICE_IDS
    chain = build_tts_provider_chain("char-Adol")
    assert [provider.name for provider in chain] == ["elevenlabs", "voai"]


def test_other_characters_get_voai_first():
    chain = build_tts_provider_chain("char-RO")
    assert [provider.name for provider in chain] == ["voai", "elevenlabs"]


def test_explicit_preferred_provider_overrides_character_default():
    chain = build_tts_provider_chain("char-Adol", "voai")
    assert [provider.name for provider in chain] == ["voai", "elevenlabs"]


def test_chain_always_has_both_providers_in_reverse_order():
    elevenlabs_first = build_tts_provider_chain("char-Adol")
    voai_first = build_tts_provider_chain("char-RO")
    assert {provider.name for provider in elevenlabs_first} == {"elevenlabs", "voai"}
    assert [provider.name for provider in elevenlabs_first] == list(reversed([provider.name for provider in voai_first]))


def test_voai_only_advances_on_fast_fail():
    voai = next(provider for provider in build_tts_provider_chain("char-RO") if provider.name == "voai")
    assert voai.should_advance_on_failure({"fast_fail": True}) is True
    assert voai.should_advance_on_failure({"fast_fail": False}) is False
    assert voai.should_advance_on_failure({}) is False


def test_elevenlabs_always_advances_on_failure():
    elevenlabs = next(provider for provider in build_tts_provider_chain("char-Adol") if provider.name == "elevenlabs")
    assert elevenlabs.should_advance_on_failure({}) is True
    assert elevenlabs.needs_fallback_timeout_grace is True


def test_voai_fallback_reason_reads_fast_fail_field():
    voai = next(provider for provider in build_tts_provider_chain("char-RO") if provider.name == "voai")
    assert voai.fallback_reason("some message", {"fast_fail": "http_401"}) == "http_401"


def test_elevenlabs_fallback_reason_reads_message():
    elevenlabs = next(provider for provider in build_tts_provider_chain("char-Adol") if provider.name == "elevenlabs")
    assert elevenlabs.fallback_reason("quota exceeded", {}) == "quota exceeded"
    assert elevenlabs.fallback_reason("", {}) == "elevenlabs_failed"


# --------------------------------------------------------------------------
# 2.3 訊號接線基準:假 worker 只實作 StreamingTTSWorker,驗證四個訊號都被
# tts_playback 正確接線並轉發到 MotionCoordinator 對應 handler。
# --------------------------------------------------------------------------

class _FakeStreamingWorker(QObject):
    """只實作 StreamingTTSWorker 契約,不牽動任何真正的供應商程式碼。"""

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)
    finished = pyqtSignal()

    def __init__(self, request: TtsRequest, parent=None):
        super().__init__(parent)
        self.request = request

    def start(self):
        pass

    def isRunning(self):
        return False

    def quit(self):
        pass

    def wait(self, timeout_ms: int = 5000) -> bool:
        return True


def test_fake_worker_declares_every_streaming_tts_worker_member():
    for member in STREAMING_TTS_WORKER_MEMBERS:
        assert hasattr(_FakeStreamingWorker, member), f"missing {member}"


class _FakeCoordinator(TtsPlaybackMixin, QObject):
    """只餵 `_start_next_tts_worker` 需要的最小狀態,不牽動真正的 MotionCoordinator。

    真正的 MotionCoordinator 是 QObject 子類(worker 建構時的 parent=self 才合法),
    這裡跟著繼承,否則傳給 QThread/QObject-based worker 的 parent 型別檢查會炸。
    """

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
        self.received_audio = []
        self.received_results = []
        self.received_progress = []

    def _current_character_id(self):
        return self._character_id

    def _on_tts_finished(self, reply_id, success, message, payload):
        self.received_results.append((reply_id, success, message, payload))

    def _on_tts_progress(self, event_name, payload):
        self.received_progress.append((event_name, payload))

    def _record_spoken_reply(self, *_a, **_k):
        pass

    def _finish_loop_action_if_tts_idle(self):
        pass


def test_all_four_signals_are_wired_and_forwarded():
    coordinator = _FakeCoordinator(_FakeStreamingWorker, "char-Adol")
    coordinator._pending_tts_chunks.put(("reply-1", "hello", "trace-1"))

    coordinator._start_next_tts_worker()
    worker = coordinator._active_tts_worker
    assert isinstance(worker, _FakeStreamingWorker)

    worker.progress_signal.emit("stream_started", {"reply_id": "reply-1"})
    worker.audio_ready_signal.emit(b"pcm-bytes", "reply-1", "trace-1")
    worker.finished_signal.emit(True, "done", {"outcome": "success"})

    assert coordinator.received_progress == [("stream_started", {"reply_id": "reply-1"})]
    assert coordinator.received_results == [("reply-1", True, "done", {"outcome": "success"})]
    coordinator._audio_worker.enqueue.assert_called_once_with(b"pcm-bytes", "reply-1", "trace-1")
