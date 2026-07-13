"""Regression test: ffplay PCM playback must not pass the removed -ac option.

ffmpeg 8.x dropped -ac for the raw PCM demuxer in favor of -ch_layout; passing
-ac makes ffplay exit immediately and breaks the stdin pipe with
OSError(22, 'Invalid argument').
"""

from unittest.mock import MagicMock

from audio_playback import FfplayPcmAudioPlayer


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


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
