"""延遲優化(optimize-e2e-turn-latency):log 去重、體感延遲欄位、串流 payload、記憶閘門、首句切分。"""
import logging
from types import SimpleNamespace

import pytest

import config
from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.engine.harness_engine import PetHarnessEngine, _SentenceSplitter
from pet_harness.latency import TurnTimeline
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType


# --- latency log ---------------------------------------------------------------

def _timeline(**marks):
    timeline = TurnTimeline.create("t1", "voice", vad_endpoint=False)
    for name, value in {"vad_endpoint": 0.0, "audio_play_started": 2.4, **marks}.items():
        timeline.mark(name, value)
    timeline.set_context(character_id="c", route_kind="conversation", skill_name=None, streaming=True, slow_tool=False)
    return timeline


def test_report_has_vad_hangover_and_perceived_latency(monkeypatch):
    monkeypatch.setattr(config, "STT_VAD_SILENCE_MS", 750)
    report = _timeline().report(**_timeline().context)
    assert report["endpoint_to_first_audio_ms"] == 2400
    assert report["vad_hangover_ms"] == 750 and report["perceived_first_audio_ms"] == 3150
    assert report["budget_exceeded"] is False  # 預算仍以 endpoint_to_first_audio 判定


def test_text_turn_has_no_hangover():
    timeline = TurnTimeline.create("t2", "text")
    report = timeline.report(character_id="c", route_kind="conversation", skill_name=None, streaming=False, slow_tool=False)
    assert report["vad_hangover_ms"] is None and report["perceived_first_audio_ms"] is None


def test_identical_relogs_are_suppressed_but_new_information_is_logged(caplog):
    timeline = _timeline()
    with caplog.at_level(logging.INFO, logger="pet_harness.latency"):
        timeline.log_current()
        timeline.log_current()  # 例如 MP3 路徑每句都會呼叫一次
        assert sum("[TURN LATENCY]" in r.message for r in caplog.records) == 1
        timeline.mark("tool_started", 1.0)
        timeline.mark("tool_done", 3.0)  # 慢工具完成後的補記
        timeline.log_current()
    assert sum("[TURN LATENCY]" in r.message for r in caplog.records) == 2


# --- Ollama payload ------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.payloads = []

    def __call__(self, method, url, timeout, json=None, stream=False):
        self.payloads.append(json)
        return SimpleNamespace(status_code=200, iter_lines=lambda: iter([b'{"response":"hi","done":true}']), close=lambda: None,
                               json=lambda: {"response": "hi"})


def _provider(recorder):
    cfg = ProviderConfig(provider_type=ProviderType.OLLAMA, base_url="http://x", model_name="m", metadata={"format": "json", "options": {"num_ctx": 4096}})
    return OllamaProvider(cfg, request_fn=recorder)


def test_stream_and_blocking_requests_send_the_same_format_and_options():
    recorder = _Recorder()
    provider = _provider(recorder)
    provider.generate_reply(UserEvent(text="hi"))
    list(provider.generate_reply_stream(UserEvent(text="hi")))
    blocking, streaming = recorder.payloads
    assert streaming["stream"] is True and blocking["stream"] is False
    for key in ("format", "options", "model", "keep_alive"):
        assert streaming[key] == blocking[key]
    assert streaming["format"] == "json"


# --- 記憶抽取閘門 --------------------------------------------------------------

@pytest.mark.parametrize("text,tool,expected", [
    ("播放輕鬆的音樂", object(), False),      # 工具輪
    ("今天有什麼遊戲新聞", object(), False),
    ("好", None, False),                       # 短句、無第一人稱
    ("你好嗎", None, False),
    ("我愛吃", None, True),                    # 短但有第一人稱
    ("I love ramen", None, True),
    ("hi", None, False),
    ("最近天氣變冷了你有沒有注意", None, True),  # 夠長
    ("how is the weather today", None, True),
    ("myth", None, False),                     # 4 字;「my」不能在單字中間誤判
])
def test_memory_extraction_gate(text, tool, expected):
    assert PetHarnessEngine._worth_extracting(text, SimpleNamespace(tool_request=tool)) is expected


# --- 首句切分 ------------------------------------------------------------------

def _feed(text):
    splitter, out = _SentenceSplitter(), []
    for char in text:
        out += splitter.feed(char)
    return out + splitter.flush()


def test_first_chunk_is_cut_at_first_comma_after_eight_chars():
    assert _feed("今天天氣真的很好呢，我們去散步吧。") == ["今天天氣真的很好呢，", "我們去散步吧。"]


def test_short_first_clause_is_not_cut_and_later_clauses_keep_old_rules():
    assert _feed("好的，我來幫你看看今天的新聞，等我一下。") == ["好的，我來幫你看看今天的新聞，", "等我一下。"]


def test_action_prefix_does_not_count_toward_first_chunk_length():
    assert _feed("[ACTION:happy] 好的，沒問題呀，我們開始吧。") == ["好的，沒問題呀，", "我們開始吧。"]
