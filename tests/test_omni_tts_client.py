"""char-Omni 本機 TTS:worker 回應驗證、路由、runtime mode、URL 驗證、問候快取、晚到音訊 guard、失敗摘要。"""

from __future__ import annotations

import io
import queue
import wave
from unittest.mock import MagicMock

import pytest
import requests
from PyQt5.QtCore import QObject

import config
from api_client import greeting_audio_cache as gac
from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker
from api_client.omni_tts_client import OmniHealthProbe, OmniLocalTTSWorker
from api_client.tts_contract import TtsProvider, TtsRequest, build_tts_provider_chain
from tts_playback import TtsPlaybackMixin


def _wav(frames=2205, rate=22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames)
    return buf.getvalue()


WAV = _wav()


class _Resp:
    def __init__(self, status=200, body=WAV, content_type="audio/wav"):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type, "X-Synth-Seconds": "0.200"}


def _req(guard=None, trace="turn-1"):
    return TtsRequest(text="你好 R O G", reply_id="r1", trace_id=trace, character_id="char-omni",
                      voice_id="v", model_id="m", playback_guard=guard)


def _run(post, guard=None):
    calls, audio, results = [], [], []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return post() if callable(post) else post

    worker = OmniLocalTTSWorker(_req(guard), requests_post=fake_post)
    worker.audio_ready_signal.connect(lambda buf, r, t: audio.append(buf.getvalue()))
    worker.finished_signal.connect(lambda ok, msg, payload: results.append((ok, msg, payload)))
    worker.run()  # 同步跑 run,不開執行緒
    return worker, calls, audio, results


def test_success_emits_wav_bytesio_and_queued_payload_without_fake_playback_events():
    worker, calls, audio, results = _run(_Resp())
    progress = []
    worker.progress_signal.connect(lambda *a: progress.append(a))
    url, kw = calls[0]
    assert url == f"{config.OMNI_TTS_BASE_URL}/tts"
    assert kw["json"] == {"text": "你好 R O G"} and kw["timeout"] == (1, 4)
    assert audio == [WAV]
    ok, _, payload = results[0]
    assert ok and payload["queued_playback"] and payload["format"] == "wav" and payload["transport"] == "http"
    assert progress == []  # 不偽造 driver_started/playback_started


def _raise():
    raise requests.ConnectionError("refused")


@pytest.mark.parametrize("post, code", [
    (_raise, "connection_failed"),
    (_Resp(503, b'{"error":"busy"}', "application/json"), "busy"),
    (_Resp(500, b"{}", "application/json"), "http_500"),
    (_Resp(200, b""), "invalid_wav"),
    (_Resp(200, WAV, "text/plain"), "invalid_content_type"),
    (_Resp(200, WAV[:-100]), "truncated_wav"),
    (_Resp(200, _wav(frames=0)), "empty_wav"),
])
def test_failures_are_critical_and_never_queue_audio(post, code):
    _, calls, audio, results = _run(post)
    assert len(calls) == 1  # 不重試
    assert audio == []
    ok, _, payload = results[0]
    assert not ok and payload["failure_code"] == code and payload["critical_tts_failure"] is True


def test_guard_rejection_discards_late_audio_without_critical():
    _, _, audio, results = _run(_Resp(), guard=lambda t, r: False)
    assert audio == []
    ok, _, payload = results[0]
    assert not ok and payload["suppressed"] and "critical_tts_failure" not in payload


def test_quit_before_response_discards():
    holder = {}

    def post():
        holder["w"].quit()  # 模擬等待中被 barge-in
        return _Resp()

    worker = OmniLocalTTSWorker(_req(), requests_post=lambda url, **kw: post())
    holder["w"] = worker
    audio, results = [], []
    worker.audio_ready_signal.connect(lambda *a: audio.append(a))
    worker.finished_signal.connect(lambda *a: results.append(a))
    worker.run()
    assert audio == [] and results[0][2]["suppressed"]


# ---- 路由 / runtime mode / URL ----

def test_omni_character_id_matches_manifest_runtime_id():
    """runtime 用 manifest 的 id(char-omni),不是資料夾名 char-Omni;寫錯會整條本機路由靜默失效。"""
    import json

    manifest = config.PROJECT_ROOT / "assets" / "characters" / "char-Omni" / "manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["id"] == config.OMNI_TTS_CHARACTER_ID


@pytest.mark.parametrize("text, preferred", [("你好", ""), ("Hello there, how are you?", ""), ("你好", "elevenlabs")])
def test_enabled_omni_chain_is_local_only(monkeypatch, text, preferred):
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    chain = build_tts_provider_chain("char-omni", preferred, text)
    assert [p.name for p in chain] == ["omni_local"]
    assert chain[0].supports_greeting_cache is False


def test_disabled_omni_and_other_characters_keep_cloud_chain(monkeypatch):
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", False)
    assert [p.name for p in build_tts_provider_chain("char-omni", "", "你好")] == ["elevenlabs", "voai"]  # 專屬聲線優先
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    assert "omni_local" not in [p.name for p in build_tts_provider_chain("char-RO", "", "你好")]
    assert "omni_local" not in [p.name for p in build_tts_provider_chain("char-Omni", "", "你好")]  # 資料夾名不是 id


def test_runtime_mode_is_local_without_cloud_keys_and_legacy_call_unchanged(monkeypatch):
    for key in ("VOAI_API_KEY", "VoAI_API_KEY", "VOAI_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    assert config.resolve_tts_runtime_mode("char-omni")[0] == "omni_local_only"
    assert config.resolve_tts_runtime_mode() == ("fallback_enabled", "voai_api_key_missing")
    assert config.resolve_tts_runtime_mode("char-RO")[0] == "fallback_enabled"
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", False)
    assert config.resolve_tts_runtime_mode("char-omni")[0] == "fallback_enabled"


@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1:8760", "http://127.0.0.1:8760"),
    ("http://localhost:9000/", "http://localhost:9000"),
    ("http://127.0.0.2:8760", "http://127.0.0.2:8760"),
    ("https://127.0.0.1:8760", None),
    ("http://192.168.1.5:8760", None),
    ("http://[::1]:8760", None),
    ("http://user:pw@127.0.0.1:8760", None),
    ("http://127.0.0.1:8760/tts", None),
    ("http://127.0.0.1:8760?x=1", None),
    ("http://127.0.0.1:8760#f", None),
    ("http://127.0.0.1", None),
    ("http://127.0.0.1:99999", None),
    ("http://evil.com:8760", None),
])
def test_loopback_url_validation(url, expected):
    assert config._parse_loopback_http_url(url) == expected


# ---- 問候快取 / guard:走真實 _start_next_tts_worker ----

class _Worker(QObject):
    from PyQt5.QtCore import pyqtSignal

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)
    finished = pyqtSignal()

    def start(self):
        pass


class _Coordinator(TtsPlaybackMixin, QObject):
    def __init__(self, character_id):
        super().__init__()
        self.made = []
        self._tts_worker_factory = lambda request, parent=None: self.made.append(request) or _Worker()
        self._character_id = character_id
        self._active_tts_worker = None
        self._pending_tts_chunks = queue.Queue()
        self._suppressed_traces = {}
        self._tts_not_expected_traces = set()
        self._trace_tts_providers = {}
        self._audio_worker = MagicMock()
        self._workers = []

    def _current_character_id(self):
        return self._character_id

    _on_tts_finished = _on_tts_progress = _record_spoken_reply = _finish_loop_action_if_tts_idle = (
        lambda self, *a, **k: None
    )


def _start(c, trace="greeting-1"):
    c._pending_tts_chunks.put(("r1", "今天過得好嗎?", trace))
    c._start_next_tts_worker()
    return c.made[-1] if c.made else None


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gac, "CACHE_DIR", tmp_path / "greetings")


@pytest.mark.parametrize("trace", ["greeting-1", "demo-1"])
def test_omni_greeting_bypasses_existing_cloud_pcm(cache_dir, monkeypatch, trace):
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    c = _Coordinator("char-omni")
    probe = TtsRequest(text="今天過得好嗎?", reply_id="x", trace_id=trace, character_id="char-omni",
                       voice_id=config.get_elevenlabs_voice_id_for_character("char-omni"),
                       model_id=config.get_elevenlabs_model_id_for_character("char-omni"))
    gac.store(probe, b"\x01\x00" * 100, 24000)  # 預置舊雲端 PCM
    request = _start(c, trace)
    assert request is not None  # 沒命中快取,建了 worker
    assert request.resolved_tts_mode == "omni_local_only"
    assert not isinstance(request.pcm_stream_sink, gac.RecordingSink)  # 不寫 PCM
    assert len(list(gac.CACHE_DIR.glob("*.pcm"))) == 1  # 舊檔沒刪

    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", False)  # rollback 後舊快取可命中
    c2 = _Coordinator("char-omni")
    assert _start(c2, trace) is None
    assert isinstance(c2._active_tts_worker, gac.CachedGreetingWorker)
    assert c2._active_tts_worker.wait(2000)  # 真的 QThread:跑完才能讓 c2 被回收,否則 qFatal


def test_reset_generation_drops_late_audio_even_after_suppressed_set_cleared(monkeypatch):
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    c = _Coordinator("char-omni")
    request = _start(c, "turn-1")
    assert request.playback_guard("turn-1", "r1") is True
    c._tts_generation += 1  # reset_runtime_state 的效果;_suppressed_traces 仍是空的
    assert request.playback_guard("turn-1", "r1") is False
    c._active_tts_worker.audio_ready_signal.emit(io.BytesIO(WAV), "r1", "turn-1")
    c._audio_worker.enqueue.assert_not_called()


def test_suppressed_trace_drops_late_audio(monkeypatch):
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    c = _Coordinator("char-omni")
    _start(c, "turn-1")
    c._suppressed_traces["turn-1"] = "barge_in"
    c._active_tts_worker.audio_ready_signal.emit(io.BytesIO(WAV), "r1", "turn-1")
    c._audio_worker.enqueue.assert_not_called()


# ---- 失敗摘要語意(provider-neutral) ----

class _ScriptedWorker(_Worker):
    def __init__(self, ok, payload=None):
        super().__init__()
        self._ok, self._payload = ok, payload

    def start(self):
        self.finished_signal.emit(self._ok, "msg", self._payload)


def _adaptive(*steps):
    chain = tuple(TtsProvider(name=name, factory=lambda request, parent=None, s=(ok, p): _ScriptedWorker(*s),
                              should_advance_on_failure=lambda payload: True)
                  for name, ok, p in steps)
    worker = AdaptiveTTSFallbackWorker(_req(), chain=chain)
    results, progress = [], []
    worker.finished_signal.connect(lambda *a: results.append(a))
    worker.progress_signal.connect(lambda *a: progress.append(a[0]))
    worker.start()
    return results[0], progress


def test_local_failure_summary_has_no_selected_provider():
    (ok, _, payload), progress = _adaptive(("omni_local", False, {"critical_tts_failure": True}))
    assert not ok and payload["attempted_providers"] == ["omni_local"]
    assert payload["selected_provider"] is None and payload["critical_tts_failure"]
    assert "critical_tts_failure" in progress


def test_local_success_selects_omni_local():
    (ok, _, payload), _ = _adaptive(("omni_local", True, {}))
    assert ok and payload["selected_provider"] == "omni_local" and payload["outcome"] == "success"


def test_cloud_fallback_success_and_all_failed_regression():
    (ok, _, payload), progress = _adaptive(("voai", False, {}), ("elevenlabs", True, {}))
    assert ok and payload["selected_provider"] == "elevenlabs" and "fallback_triggered" in progress
    (ok, _, payload), progress = _adaptive(("voai", False, {}), ("elevenlabs", False, {}))
    assert not ok and payload["selected_provider"] is None
    assert payload["attempted_providers"] == ["voai", "elevenlabs"] and "critical_tts_failure" in progress


# ---- health probe ----

def test_health_probe_runs_off_caller_thread_and_reports_unreachable():
    import threading

    callers = []

    def fake_get(url, **kw):
        callers.append(threading.current_thread().name)
        raise requests.ConnectionError("down")

    probe = OmniHealthProbe(requests_get=fake_get)
    probe.refresh()
    for t in threading.enumerate():
        if t.name == "omni-tts-health":
            t.join(2)
    assert callers == ["omni-tts-health"]
    assert probe.ready is False and "unreachable" in probe.last_error


# ---- 容器播放:WAV 必須用 wav namehint,MP3 維持原樣 ----

def test_pygame_player_picks_namehint_from_container():
    from audio_playback import PygameInMemoryAudioPlayer

    loaded = []
    music = MagicMock()
    music.load.side_effect = lambda buf, hint: loaded.append((buf.read(4), hint))
    music.get_busy.return_value = False
    mixer = MagicMock(music=music)
    mixer.get_init.return_value = True
    player = PygameInMemoryAudioPlayer(mixer_module=mixer)
    player.play(io.BytesIO(WAV))
    player.play(io.BytesIO(b"ID3\x04fake-mp3"))
    assert loaded == [(b"RIFF", "wav"), (b"ID3\x04", "mp3")]


# ---- harness 狀態:跟 active character 走,讀背景 health 結果 ----

def _adapter(character_id):
    from types import SimpleNamespace

    from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
    from pet_harness.voice_runtime_status_adapter import VoiceRuntimeStatusAdapter

    adapter = PyQtHarnessAdapter.__new__(PyQtHarnessAdapter)
    adapter.router = SimpleNamespace(get_active_snapshot=lambda: SimpleNamespace(character_id=character_id))
    adapter._voice_status_adapter = VoiceRuntimeStatusAdapter()
    return adapter


@pytest.mark.parametrize("ready, expected", [(False, "configured_missing_runtime"), (True, "configured_and_ready")])
def test_harness_status_reflects_omni_health_without_cloud_env(monkeypatch, ready, expected):
    from api_client import omni_tts_client

    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    monkeypatch.setattr(omni_tts_client.OMNI_HEALTH, "refresh", lambda: None)  # GUI thread 不發 HTTP
    monkeypatch.setattr(omni_tts_client.OMNI_HEALTH, "ready", ready)
    monkeypatch.setattr(omni_tts_client.OMNI_HEALTH, "last_error", "" if ready else "omni_tts_unreachable: ConnectionError")
    adapter = _adapter("char-omni")
    status = adapter._build_tts_provider_status()
    assert status["resolved_mode"] == "omni_local_only" and status["status"] == expected
    voice = adapter._build_voice_status()
    assert voice["tts"]["provider"] == "omni_local" and voice["tts"]["status"] == expected
    assert not any("ELEVENLABS" in env for env in voice["tts"]["required_env"])
    if not ready:
        assert "unreachable" in voice["tts"]["message"]


def test_harness_status_for_other_character_is_cloud(monkeypatch):
    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", True)
    adapter = _adapter("char-RO")
    assert adapter._build_tts_provider_status()["resolved_mode"] != "omni_local_only"
    assert adapter._build_voice_status()["tts"]["provider"] == "adaptive"


# ---- 自動啟動 run.bat ----

def _autostart(monkeypatch, *, enabled=True, health=None):
    from api_client import omni_tts_client

    monkeypatch.setattr(config, "OMNI_TTS_ENABLED", enabled)
    calls = []

    def get(*_a, **_k):
        if health is None:
            raise requests.ConnectionError("down")
        return MagicMock(status_code=health)

    thread = omni_tts_client.ensure_omni_service_started(get, lambda *a, **k: calls.append((a, k)))
    if thread:
        thread.join(2)
    return calls


def test_autostart_runs_bat_only_when_service_down(monkeypatch):
    monkeypatch.setattr(config, "OMNI_TTS_BASE_URL", "http://127.0.0.1:8799")
    calls = _autostart(monkeypatch)
    assert len(calls) == 1 and calls[0][0][0][-1].endswith("run.bat")
    assert calls[0][1]["env"]["PORT"] == "8799"
    assert _autostart(monkeypatch, health=200) == []
    assert _autostart(monkeypatch, enabled=False) == []
