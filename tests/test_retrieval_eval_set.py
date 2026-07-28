import json
from pathlib import Path


def test_eval_set_has_13_text_ground_truth_cases():
    cases = json.loads(Path("tests/data/retrieval_eval_set.json").read_text(encoding="utf-8"))
    required = {"scenario_id", "previous_user", "previous_assistant", "current_turn", "expected_facts", "forbidden_facts"}
    assert len(cases) == 13
    assert all(required <= case.keys() for case in cases)
    assert all(isinstance(fact, str) for case in cases for fact in case["expected_facts"] + case["forbidden_facts"])
