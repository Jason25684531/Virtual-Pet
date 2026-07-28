from pet_harness.memory.memory_models import RetrievalRequest
from pet_harness.memory.query_rewriter import FollowUpDetector, LlmQueryRewriter
from pet_harness.memory.query_rewriter import previous_turn
from datetime import UTC, datetime, timedelta


def test_follow_up_detector_identifies_short_turn_with_previous_context():
    assert FollowUpDetector().detect(RetrievalRequest("miku", "小白", "我養了一隻貓")) == "short"


def test_rewriter_fails_open():
    rewriter = LlmQueryRewriter(lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()))
    assert rewriter.rewrite(RetrievalRequest("miku", "那個", "我喜歡蘋果")) is None


def test_previous_turn_expires_after_thirty_minutes():
    event = {"created_at": (datetime.now(UTC) - timedelta(seconds=1801)).isoformat(), "input_payload": {"text": "old"}, "output_payload": {"reply": "old"}}
    assert previous_turn([event], datetime.now(UTC)) == (None, None, None)


def test_follow_up_detector_covers_pronoun_and_assistant_question():
    detector = FollowUpDetector()
    assert detector.detect(RetrievalRequest("miku", "那個呢？", "先前問題")) == "pronoun"
    assert detector.detect(RetrievalRequest("miku", "請繼續詳細說明目前已經討論的內容", "先前問題", "要我繼續嗎？")) == "assistant_question"


def test_llm_rewriter_is_disabled_by_default_and_uses_timeout_when_enabled():
    calls = []

    def call(_request, *, timeout):
        calls.append(timeout)
        return "獨立問題"

    request = RetrievalRequest("miku", "那個呢？", "先前問題")
    assert LlmQueryRewriter(call).rewrite(request) is None
    assert LlmQueryRewriter(call, enabled=True).rewrite(request) == "獨立問題"
    assert calls == [1.25]
