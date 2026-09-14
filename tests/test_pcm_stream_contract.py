"""voice-motion-sync:PCM 格式契約、frame 邊界、工作階段隔離與遲到音訊丟棄。

用可重現的 provider bytes 覆蓋 4.1–4.4;實機聽測(4.6)不在這裡,另行記錄。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import config
from audio_playback import FfplayPcmAudioPlayer, detect_audio_container, is_raw_pcm_content_type
from audio_worker import AudioStreamWorker
from pet_harness.character import generation as character_generation


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
        text="測試", reply_id="reply-1", trace_id="trace-1", voice_id="voice",
        pcm_stream_sink=sink, requests_post=fake_post,
    )
    worker.finished_signal.connect(lambda ok, message, payload: results.append((ok, message)))
    worker.run()

    assert results and results[0][0] is False
    assert "audio/mpeg" in results[0][1]
    sink.enqueue_pcm_chunk.assert_not_called()


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
