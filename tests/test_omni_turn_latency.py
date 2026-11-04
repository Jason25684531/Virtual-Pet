"""omni-response-under-2500ms 4.4:容器音訊首音、文字回合起點、Omni 2.5 秒預算、Ollama 時間拆解。"""
import queue
from types import SimpleNamespace

import config
from audio_worker import AudioStreamWorker
from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.latency import TurnTimeline, create_turn, get_turn
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType

_CTX = dict(route_kind="conversation", skill_name=None, streaming=True, slow_tool=False)


def _report(origin, character_id, **marks):
    timeline = TurnTimeline.create("t", origin)
    for name, value in marks.items():
        timeline.mark(name, value)
    return timeline.report(character_id=character_id, **_CTX)


def test_container_enqueue_marks_first_audio_so_audio_start_is_computable():
    worker = AudioStreamWorker.__new__(AudioStreamWorker)
    worker._queue = queue.Queue()
    timeline = create_turn("wav-turn", "text")
    worker.enqueue(object(), "r1", "wav-turn")
    timeline.mark("audio_play_started")
    assert get_turn("wav-turn").checkpoints["tts_first_pcm"] is not None
    assert timeline.report(character_id="char-omni", **_CTX)["audio_start_ms"] is not None


def test_text_turn_measures_from_input_and_flags_omni_budget():
    report = _report("text", "char-omni", input_submitted=0.0, audio_play_started=2.9)
    assert report["input_to_first_audio_ms"] == 2900 and report["endpoint_to_first_audio_ms"] is None
    assert report["omni_budget_exceeded"] is True
    assert _report("text", "char-omni", input_submitted=0.0, audio_play_started=1.2)["omni_budget_exceeded"] is False


def test_voice_turn_measures_from_stt_done_alongside_endpoint_metric():
    report = _report("vad", "char-omni", vad_endpoint=0.0, stt_done=0.4, audio_play_started=2.0)
    assert report["input_to_first_audio_ms"] == 1600 and report["endpoint_to_first_audio_ms"] == 2000


def test_other_characters_never_get_omni_budget_flag():
    assert _report("text", "Choppr", input_submitted=0.0, audio_play_started=2.9)["omni_budget_exceeded"] is None


def test_no_audio_is_unmeasurable_not_a_pass():
    report = _report("text", "char-omni", input_submitted=0.0)
    assert report["input_to_first_audio_ms"] is None and report["omni_budget_exceeded"] is None


def test_prompt_prefix_is_identical_across_turns_so_ollama_can_reuse_it():
    builder = PromptBuilder(".agentic")
    tags = ["laugh", "listen", "awkward"]
    common = dict(skills=[], persona="我是 OMNI。", action_tags=tags)
    first = builder.build(UserEvent(text="你好"), state_snapshot={"user_progress": {"xp": 1}}, **common).prompt
    second = builder.build(
        UserEvent(text="跟我說笑話"), state_snapshot={"user_progress": {"xp": 9}}, last_action_tag="laugh",
        conversation_history=[{"input_payload": {"text": "你好"}, "output_payload": {"reply": "嗨"}}], **common,
    ).prompt
    prefix = builder.stable_prefix([], "我是 OMNI。", tags)
    assert first.startswith(prefix) and second.startswith(prefix)
    assert "## Output Contract" in prefix and "## Current Pet State" not in prefix and "## User Text" not in prefix
    assert "choose an action_tag other than laugh" in second and "other than" not in first


def test_ollama_stream_stats_land_on_turn_timeline():
    timeline = create_turn("llm-turn", "text")
    lines = [b'{"response":"hi","done":false}',
             b'{"response":"","done":true,"load_duration":8000000,"prompt_eval_duration":1600000000,"prompt_eval_count":3900}']
    response = SimpleNamespace(status_code=200, iter_lines=lambda: lines, close=lambda: None)
    provider = OllamaProvider(ProviderConfig(provider_type=ProviderType.OLLAMA, model_name="m"), request_fn=lambda *a, **k: response)
    event = UserEvent.from_dict({"text": "hi", "metadata": {"turn_id": "llm-turn"}})
    assert list(provider.generate_reply_stream(event)) == ["hi"]
    report = timeline.report(character_id="char-omni", **_CTX)
    assert (report["llm_load_ms"], report["llm_prompt_eval_ms"], report["llm_prompt_tokens"]) == (8, 1600, 3900)
