from dataclasses import FrozenInstanceError

import pytest

from pet_harness.memory.memory_models import RetrievalRequest, RetrievalTrace


def test_retrieval_request_and_trace_are_immutable_public_values():
    request = RetrievalRequest(character_id="miku", current_turn_text="那個呢")
    trace = RetrievalTrace.empty("那個呢")

    assert request.top_k == 5
    assert trace.rewrite_tier == 2
    with pytest.raises(FrozenInstanceError):
        request.top_k = 3
