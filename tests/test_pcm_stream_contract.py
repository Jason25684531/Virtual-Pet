"""voice-motion-sync:PCM 格式契約、frame 邊界、工作階段隔離與遲到音訊丟棄。

用可重現的 provider bytes 覆蓋 4.1–4.4;實機聽測(4.6)不在這裡,另行記錄。
"""

from __future__ import annotations

import logging
import struct
import threading
from unittest.mock import MagicMock

import pytest

import config
from api_client.tts_contract import TtsRequest
from audio_playback import FfplayPcmAudioPlayer, detect_audio_container, is_raw_pcm_content_type
from audio_worker import AudioStreamWorker
from pet_harness.character import generation as character_generation


def _elevenlabs_request(*, model_id: str = "", pcm_stream_sink=None):
    return TtsRequest(
        text="測試", reply_id="reply-1", trace_id="trace-1", character_id="voice",
        voice_id="voice", model_id=model_id, pcm_stream_sink=pcm_stream_sink,
    )


class _RecordingPlayer:
    def __init__(self):
        self.chunks: list[bytes] = []

    def is_available(self):
        return True

    def play_chunks(self, chunks, before_start=None):
        if callable(before_start):
            before_start()
        for chunk in chunks:
            self.chunks.append(chunk)
        return sum(len(chunk) for chunk in self.chunks)


def _worker() -> tuple[AudioStreamWorker, list[_RecordingPlayer]]:
    players: list[_RecordingPlayer] = []

    def factory(_rate, _channels):
        players.append(_RecordingPlayer())
        return players[-1]

    return AudioStreamWorker(pcm_player_factory=factory), players


# --------------------------------------------------------------------------
# 4.1 格式契約
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "head,expected",
    [
        (b"RIFF\x00\x00\x00\x00WAVE", "wav"),
        (b"ID3\x03\x00", "mp3"),
        (b"\xff\xfb\x90\x00", "mp3"),
        (b"OggS\x00", "ogg"),
        (b'{"detail":"rate limited"}', "text"),
        (b"<html><body>502 Bad Gateway", "text"),
        (b"<html>", None),  # 太短的前綴不猜,合法 PCM 也可能長這樣
        (b"\x01\x00\x02\x00\x03\x00", None),  # 真正的 s16le
    ],
)
def test_container_and_error_payloads_are_detected(head, expected):
    assert detect_audio_container(head) == expected


@pytest.mark.parametrize(
    "content_type,ok",
    [
        ("audio/pcm", True),
        ("audio/L16;rate=24000", True),
        ("application/octet-stream", True),
        ("audio/mpeg", False),          # 要 PCM 卻拿到 MP3 —— 舊檢查會放行
        ("audio/wav", False),
        ("application/json", False),
        ("", False),
    ],
)
def test_only_raw_pcm_content_types_reach_the_pcm_path(content_type, ok):
    assert is_raw_pcm_content_type(content_type) is ok


@pytest.mark.parametrize("payload", [b"RIFF\x00\x00\x00\x00WAVEfmt ", b"ID3\x04\x00\x00", b'{"error":"quota exceeded for this key"}'])
def test_worker_refuses_container_or_error_bytes_instead_of_playing_noise(payload):
    """4.1:所有 provider 都經過 enqueue_pcm_chunk,防護放在這一層才擋得住全部來源。"""
    worker, players = _worker()

    with pytest.raises(ValueError, match="raw PCM sink"):
        worker.enqueue_pcm_chunk(payload, "reply-1", "trace-1")
    assert players == []
    assert worker._pcm_sessions == {}


def test_elevenlabs_rejects_mp3_when_it_asked_for_pcm():
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    def fake_post(_url, headers=None, params=None, json=None, **_kwargs):
        response = MagicMock()
        response.headers = {"content-type": "audio/mpeg"}
        response.iter_content.return_value = [b"ID3\x04\x00\x00"]
        return response

    sink = MagicMock()
    results: list[tuple] = []
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(pcm_stream_sink=sink), requests_post=fake_post,
    )
    worker.finished_signal.connect(lambda ok, message, payload: results.append((ok, message)))
    worker.run()

    assert results and results[0][0] is False
    assert "audio/mpeg" in results[0][1]
    sink.enqueue_pcm_chunk.assert_not_called()


def _fake_pcm_post(captured: dict):
    def fake_post(_url, headers=None, params=None, json=None, **_kwargs):
        captured["params"] = params
        captured["json"] = json
        response = MagicMock()
        response.headers = {"content-type": "audio/pcm"}
        response.iter_content.return_value = [b"\x00\x00" * 8]
        return response

    return fake_post


def test_explicit_model_id_is_sent_instead_of_global_env(monkeypatch):
    """per-character-tts-model 2.1/3.1:傳入的 model_id 優先於全域 ELEVENLABS_MODEL_ID。"""
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    captured: dict = {}
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(model_id="eleven_v3"), requests_post=_fake_pcm_post(captured),
    )
    worker.run()

    assert captured["json"]["model_id"] == "eleven_v3"


def test_omitted_model_id_falls_back_to_global_env(monkeypatch):
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    captured: dict = {}
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(), requests_post=_fake_pcm_post(captured),
    )
    worker.run()

    assert captured["json"]["model_id"] == "eleven_flash_v2_5"


def test_eleven_v3_request_omits_optimize_streaming_latency(monkeypatch):
    """v3 拒絕這個 query 參數(400 unsupported_model),見 elevenlabs_client.py 的模型能力表。"""
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    captured: dict = {}
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(model_id="eleven_v3"), requests_post=_fake_pcm_post(captured),
    )
    worker.run()

    assert "optimize_streaming_latency" not in captured["params"]


def test_flash_request_keeps_optimize_streaming_latency(monkeypatch):
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    captured: dict = {}
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(model_id="eleven_flash_v2_5"), requests_post=_fake_pcm_post(captured),
    )
    worker.run()

    assert "optimize_streaming_latency" in captured["params"]


def test_flash_speed_is_clamped_to_its_valid_range(monkeypatch):
    """.env 的 ELEVENLABS_SPEED=1.3 超出 flash 合法範圍(0.7-1.2),否則 400 invalid_voice_settings。"""
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    monkeypatch.setenv("ELEVENLABS_SPEED", "1.3")
    captured: dict = {}
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(model_id="eleven_flash_v2_5"), requests_post=_fake_pcm_post(captured),
    )
    worker.run()

    assert captured["json"]["voice_settings"]["speed"] == 1.2


def test_v3_speed_is_not_clamped(monkeypatch):
    """實測(design.md smoke)v3 接受超出 flash 範圍的 speed,不應被夾住。"""
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    monkeypatch.setenv("ELEVENLABS_SPEED", "1.3")
    captured: dict = {}
    worker = ElevenLabsStreamingTTSWorker(
        _elevenlabs_request(model_id="eleven_v3"), requests_post=_fake_pcm_post(captured),
    )
    worker.run()

    assert captured["json"]["voice_settings"]["speed"] == 1.3


def test_ffplay_path_none_means_unavailable_but_omitting_it_still_auto_discovers():
    assert FfplayPcmAudioPlayer(ffplay_path=None).is_available() is False
    assert FfplayPcmAudioPlayer(ffplay_path="ffplay").is_available() is True


# --------------------------------------------------------------------------
# 4.2 / 4.3 frame 邊界與 bytes→時長一致性
# --------------------------------------------------------------------------

def test_chunks_split_mid_sample_stay_frame_aligned():
    """4.2:分塊切在 16-bit 樣本中間時,播放器收到的資料仍須 frame 對齊。"""
    worker, players = _worker()
    worker.enqueue_pcm_chunk(b"\x01\x02\x03", "reply-1", "trace-1")   # 3 bytes:一個半樣本
    worker.enqueue_pcm_chunk(b"\x04\x05\x06", "reply-1", "trace-1")   # 補齊後共 6 bytes

    session = worker._pcm_sessions["trace-1"]
    assert session._segment_bytes["reply-1"] == 6
    assert session._partial == {}


def test_waveform_matches_the_unsplit_version():
    """4.3:合法分塊的波形要和未分塊版本一致。"""
    payload = bytes(range(64))
    whole, whole_players = _worker()
    whole.enqueue_pcm_chunk(payload, "reply-1", "trace-1")
    whole.finish_pcm_segment("reply-1", "trace-1")

    split, split_players = _worker()
    for start in range(0, len(payload), 3):   # 故意用非 2 的倍數切
        split.enqueue_pcm_chunk(payload[start:start + 3], "reply-1", "trace-2")
    split.finish_pcm_segment("reply-1", "trace-2")

    whole.close_trace_session("trace-1")
    split.close_trace_session("trace-2")
    whole._pcm_sessions.get("trace-1") and whole._pcm_sessions["trace-1"]._thread.join(timeout=2)
    split._pcm_sessions.get("trace-2") and split._pcm_sessions["trace-2"]._thread.join(timeout=2)

    assert b"".join(split_players[0].chunks) == b"".join(whole_players[0].chunks) == payload


def test_trailing_partial_sample_is_reported_and_dropped(caplog):
    """4.2/4.3:結尾殘片記錄失敗而不輸出雜訊,且不計進句段長度。"""
    import logging

    worker, _players = _worker()
    with caplog.at_level(logging.ERROR, logger="audio_worker"):
        worker.enqueue_pcm_chunk(b"\x01\x02\x03", "reply-1", "trace-1")
        worker.finish_pcm_segment("reply-1", "trace-1")

    session = worker._pcm_sessions["trace-1"]
    assert session._segment_bytes["reply-1"] == 2       # 只算完整樣本
    assert any("殘留不完整樣本" in record.message for record in caplog.records)


def test_bytes_to_duration_uses_the_session_sample_rate():
    worker, _players = _worker()
    worker.enqueue_pcm_chunk(b"\x00\x00", "reply-1", "trace-1", sample_rate=24000)
    assert worker._pcm_sessions["trace-1"]._bytes_per_second == 24000 * 2


def test_empty_response_creates_no_session():
    worker, players = _worker()
    worker.enqueue_pcm_chunk(b"", "reply-1", "trace-1")
    assert players == [] and worker._pcm_sessions == {}


def test_mismatched_sample_rate_segment_is_dropped_not_resampled():
    worker, players = _worker()
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1", sample_rate=config.TTS_PCM_SAMPLE_RATE)
    worker.enqueue_pcm_chunk(b"\x02\x02", "reply-2", "trace-1", sample_rate=config.TTS_PCM_SAMPLE_RATE + 8000)

    assert len(players) == 1
    assert "reply-2" not in worker._pcm_sessions["trace-1"]._segment_bytes


# --------------------------------------------------------------------------
# 4.4 取消、切角與遲到音訊
# --------------------------------------------------------------------------

def test_late_chunks_after_an_interrupt_are_dropped():
    worker, players = _worker()
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1")
    worker.interrupt_trace("trace-1")
    worker.enqueue_pcm_chunk(b"\x02\x02", "reply-2", "trace-1")   # TTS 還在路上

    assert len(players) == 1
    assert "reply-2" not in players[0].chunks


def test_late_chunks_after_a_character_switch_are_dropped():
    """4.4:切角後才抵達的分塊屬於上一個角色,不得混進現在的 session。"""
    worker, players = _worker()
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1")
    session = worker._pcm_sessions["trace-1"]
    before = session._segment_bytes["reply-1"]

    character_generation.advance()
    worker.enqueue_pcm_chunk(b"\x02\x02", "reply-1", "trace-1")

    assert session._segment_bytes["reply-1"] == before
    assert len(players) == 1


def test_interrupt_records_a_cancel_reason_on_the_timeline():
    from pet_harness.latency import create_turn

    timeline = create_turn("trace-cancel", "vad")
    worker, _players = _worker()
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-cancel")
    worker.interrupt_all(reason="returned_to_main_menu")

    assert timeline.cancel_reason == "returned_to_main_menu"


def test_suppressed_trace_reports_its_own_reason():
    from pet_harness.latency import create_turn

    timeline = create_turn("trace-suppress", "vad")
    worker, _players = _worker()
    worker.suppress_trace("trace-suppress")

    assert timeline.cancel_reason == "playback_suppressed"


class _BlockingPlayer:
    """play_chunks 消耗完 chunk queue 後卡在 release,讓 session 停留在
    「已關閉但 thread 尚未從 _pcm_sessions 移除」的視窗,方便測試遲到分塊。"""

    def __init__(self):
        self.release = threading.Event()

    def is_available(self):
        return True

    def play_chunks(self, chunks, before_start=None):
        if callable(before_start):
            before_start()
        for _ in chunks:
            pass
        self.release.wait(timeout=2)
        return 0


def test_late_chunk_after_session_closed_logs_warning_instead_of_dropping_silently(caplog):
    """Regression fix-streaming-pcm-session-early-close 2.1: session 已經
    close() 過,producer 還在路上送分塊時,MUST NOT 靜默丟棄——要留下可辨識的
    WARNING,且不得因此開出第二個 session/ffplay。"""
    players: list[_BlockingPlayer] = []

    def factory(_rate, _channels):
        players.append(_BlockingPlayer())
        return players[-1]

    worker = AudioStreamWorker(pcm_player_factory=factory)
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1")
    worker.close_trace_session("trace-1")

    with caplog.at_level(logging.WARNING, logger="audio_worker"):
        worker.enqueue_pcm_chunk(b"\x02\x02", "reply-1", "trace-1")

    assert any(
        "session 已關閉" in record.message and "trace-1" in record.message
        for record in caplog.records
    )
    assert len(players) == 1

    players[0].release.set()
    session = worker._pcm_sessions.get("trace-1")
    if session is not None:
        session._thread.join(timeout=2)


# --------------------------------------------------------------------------
# 4.5 中斷收尾：斜坡降到 0，不硬切
# --------------------------------------------------------------------------

def test_interrupt_appends_a_fade_out_ramp_instead_of_a_hard_cut():
    """design D4 (fix-play-music-ack-audio-and-idle-restore 4.2.8): 中斷時
    MUST NOT 把波形停在非零取樣點直接接靜音——那就是爆音。收尾要先把最後一個
    frame 斜坡降到 0。"""
    worker, players = _worker()
    worker.enqueue_pcm_chunk(struct.pack("<h", 4000), "reply-1", "trace-1")
    session = worker._pcm_sessions["trace-1"]

    worker.interrupt_trace("trace-1")
    session._thread.join(timeout=2)

    written = b"".join(players[0].chunks)
    assert len(written) > 2  # 原始 2 bytes 之外還有斜坡尾音
    assert written[-2:] == struct.pack("<h", 0)  # 斜坡最終降到 0，不是硬切
    assert session._segment_bytes["reply-1"] == 2  # 斜坡不計入任何 reply 的 segment 長度


def test_interrupt_before_any_audio_produces_no_fade_tail():
    """4.4 既有場景的延伸：suppress_trace 在第一個樣本抵達前就中斷,沒有
    _last_frame 可以斜坡,不該生出雜訊。"""
    worker, players = _worker()
    worker.suppress_trace("trace-1")

    assert players == []
