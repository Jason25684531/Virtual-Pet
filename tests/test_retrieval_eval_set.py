import json
from pathlib import Path

import pytest

from scripts.eval_harness import validate_case

pytestmark = pytest.mark.uses_repo_cwd


def cases():
    return json.loads(Path("tests/data/retrieval_eval_set.json").read_text(encoding="utf-8"))


def test_eval_set_is_self_contained_and_covers_required_categories():
    required = {"id", "category", "memories", "lifecycle", "conversation_context", "query", "expected_memory_keys", "expected_no_memory"}
    loaded = cases()
    assert all(required <= case.keys() for case in loaded)
    assert {case["category"] for case in loaded} >= {"exact", "semantic", "sparse-lexical", "distractor", "no-memory", "follow-up", "supersede"}
    assert all(not validate_case(case) for case in loaded)


def test_expected_key_not_seeded_is_invalid():
    case = cases()[0] | {"expected_memory_keys": ["missing"]}
    assert "expected keys not seeded" in validate_case(case)[0]


def test_no_memory_needs_three_distractors():
    case = cases()[4] | {"memories": cases()[4]["memories"][:2]}
    assert "at least 3 distractors" in validate_case(case)[0]


def test_ground_truth_modes_are_mutually_exclusive():
    case = cases()[0] | {"expected_memory_keys": []}
    assert "mutually exclusive" in validate_case(case)[0]
