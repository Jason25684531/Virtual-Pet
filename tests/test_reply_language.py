import pytest

from pet_harness.agent.reply_language import detect_reply_language


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What should I eat tonight?", "en"),
        ("你好，今天好嗎", "zh"),
        ("播放 Taylor Swift", "zh"),
        ("", "zh"),
        ("?!  ", "zh"),
        ("123", "zh"),
        (None, "zh"),
    ],
)
def test_detect_reply_language(text, expected):
    assert detect_reply_language(text) == expected
