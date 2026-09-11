"""Regression test: ffplay PCM playback must not pass the removed -ac option.

ffmpeg 8.x dropped -ac for the raw PCM demuxer in favor of -ch_layout; passing
-ac makes ffplay exit immediately and breaks the stdin pipe with
OSError(22, 'Invalid argument').
"""

from unittest.mock import MagicMock

import pytest

from audio_playback import FfplayPcmAudioPlayer
from audio_worker import AudioStreamWorker


def test_play_chunks_uses_ch_layout_not_ac():
    fake_process = MagicMock()
    fake_process.stdin = MagicMock()
    fake_popen_factory = MagicMock(return_value=fake_process)

    player = FfplayPcmAudioPlayer(
        ffplay_path="ffplay",
        sample_rate=32000,
        channels=1,
        popen_factory=fake_popen_factory,
    )

    player.play_chunks([b"\x00\x00"])

    args = fake_popen_factory.call_args[0][0]
    assert "-ac" not in args
    assert "-ch_layout" in args
    assert args[args.index("-ch_layout") + 1] == "1"


def test_enqueue_pcm_chunk_raises_when_ffplay_missing():
    """ffplay 缺席必須讓 producer 收到失敗，而不是每個 chunk 靜靜重試一次。

    session thread 內的例外只寫 log，呼叫端會誤判 success，pending action
    因而永遠等不到 driver_started，角色動畫卡在 idle。
    """
    unavailable = FfplayPcmAudioPlayer(ffplay_path=None)
    assert unavailable.is_available() is False

    worker = AudioStreamWorker(pcm_player_factory=lambda sample_rate, channels: unavailable)

    with pytest.raises(RuntimeError, match="ffplay"):
        worker.enqueue_pcm_chunk(b"\x00\x00", "reply-1", "trace-1")
    assert worker._pcm_sessions == {}


def test_all_tts_providers_agree_on_one_pcm_sample_rate():
    """取樣率分散在各 client 各寫一份就是 #2 的成因：同回合 fallback 到不同
    取樣率的 provider 會變速播放，且句段結束時間反推失準把句尾截掉。"""
    import config
    from api_client import voai_client

    worker = AudioStreamWorker()

    assert voai_client._PCM_SAMPLE_RATE == config.TTS_PCM_SAMPLE_RATE
    assert worker._pcm_sample_rate == config.TTS_PCM_SAMPLE_RATE
    assert worker._pcm_bytes_per_second == config.TTS_PCM_SAMPLE_RATE * 2


def test_elevenlabs_requests_the_shared_sample_rate():
    """ElevenLabs 的 PCM 格式是固定清單，共同值必須是它支援的那幾個之一。"""
    import config
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker

    captured = {}

    def fake_post(url, headers=None, params=None, json=None, **kwargs):
        captured.update(params or {})
        raise RuntimeError("stop after capturing the request")

    worker = ElevenLabsStreamingTTSWorker(
        text="測試", reply_id="reply-1", trace_id="trace-1", voice_id="voice",
        pcm_stream_sink=MagicMock(), requests_post=fake_post,
    )
    worker.run()

    assert captured["output_format"] == f"pcm_{config.TTS_PCM_SAMPLE_RATE}"
    assert config.TTS_PCM_SAMPLE_RATE in (16000, 22050, 24000, 44100)


class _RecordingPlayer:
    """記下實際灌進來的 chunk；play_chunks 會被 session thread 消費。"""

    def __init__(self):
        self.chunks = []

    def is_available(self):
        return True

    def play_chunks(self, chunks, before_start=None):
        if callable(before_start):
            before_start()
        for chunk in chunks:
            self.chunks.append(chunk)
        return sum(len(chunk) for chunk in self.chunks)


def test_mismatched_sample_rate_is_dropped_instead_of_played_at_the_wrong_speed():
    """ffplay 的 -ar 在 session 建立時鎖死。同回合換 provider 若取樣率不同，
    硬灌進去會變速播放，且句段結束時間反推失準而把尾巴截掉。"""
    players = []

    def factory(sample_rate, channels):
        player = _RecordingPlayer()
        players.append((sample_rate, player))
        return player

    worker = AudioStreamWorker(pcm_player_factory=factory)
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1", sample_rate=32000)
    worker.enqueue_pcm_chunk(b"\x02\x02", "reply-2", "trace-1", sample_rate=24000)

    # 只開了一個 session，且是用第一段的取樣率
    assert len(players) == 1
    assert players[0][0] == 32000
    # 取樣率不符的那一段不得混進同一個 ffplay
    session = worker._pcm_sessions["trace-1"]
    assert session.sample_rate == 32000
    assert "reply-2" not in session._segment_bytes


def test_matching_sample_rate_still_shares_one_session():
    worker = AudioStreamWorker(pcm_player_factory=lambda rate, channels: _RecordingPlayer())
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1", sample_rate=24000)
    worker.enqueue_pcm_chunk(b"\x02\x02", "reply-2", "trace-1", sample_rate=24000)

    session = worker._pcm_sessions["trace-1"]
    assert session.sample_rate == 24000
    assert "reply-2" in session._segment_bytes


def test_chunks_without_an_explicit_rate_are_accepted():
    """沒帶 sample_rate 的呼叫端沿用 worker 預設值，不該被防護擋掉。"""
    worker = AudioStreamWorker(pcm_player_factory=lambda rate, channels: _RecordingPlayer())
    worker.enqueue_pcm_chunk(b"\x01\x01", "reply-1", "trace-1")
    worker.enqueue_pcm_chunk(b"\x02\x02", "reply-2", "trace-1")

    assert "reply-2" in worker._pcm_sessions["trace-1"]._segment_bytes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
