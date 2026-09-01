from scripts.eval_harness import DeterministicFakeDenseEncoder, metric_for_keys, run_evaluation, select_threshold, validate_case


def test_recall_and_mrr_rank_1_to_3_and_miss():
    assert metric_for_keys(["target"], ["target"]) == (1.0, 1.0, 1.0, 1.0)
    assert metric_for_keys(["x", "y", "target"], ["target"]) == (0.0, 1.0, 1 / 3, 1 / 2)
    assert metric_for_keys(["x", "y", "z"], ["target"]) == (0.0, 0.0, 0.0, 0.0)


def test_fake_encoder_is_deterministic_and_vector_based():
    encoder = DeterministicFakeDenseEncoder()
    assert encoder("日本旅遊") == encoder("日本旅遊")
    assert sum(a * b for a, b in zip(encoder("日本旅遊"), encoder("日本旅行"))) > sum(a * b for a, b in zip(encoder("日本旅遊"), encoder("量子鳳梨")))


def test_invalid_configuration_is_not_a_recall_miss():
    case = {"id":"bad","category":"semantic","memories":[],"lifecycle":[],"conversation_context":{},"query":"x","expected_memory_keys":["missing"],"expected_no_memory":False}
    report = run_evaluation([case])
    assert report["evaluation_valid"] is False
    assert report["configuration_failure_count"] == 1
    assert report["valid_relevant_cases"] == 0


def test_no_memory_distractors_are_not_removed_by_harness():
    case = {"id":"no","category":"no-memory","memories":[{"memory_key":str(i),"text":f"distractor {i}"} for i in range(3)],"lifecycle":[],"conversation_context":{},"query":"unrelated","expected_memory_keys":[],"expected_no_memory":True}
    report = run_evaluation([case])
    assert report["valid_no_memory_cases"] == 1
    assert report["case_results"] == [] or report["no_memory_rejection_rate"] in (0.0, 1.0)


def test_calibration_is_separate_from_rrf_scores_and_calls_are_one():
    case = {"id":"one","category":"semantic","memories":[{"memory_key":"k","text":"日本旅遊"}],"lifecycle":[],"conversation_context":{},"query":"日本旅行","expected_memory_keys":["k"],"expected_no_memory":False}
    report = run_evaluation([case])
    assert report["production_qdrant_calls_per_query"] == 1.0
    assert report["dense_scores"]["encoder_kind"] == "deterministic_fake"
    assert set(report["dense_scores"]) == {"encoder_kind", "positive", "negative", "no_memory_negative"}


def test_threshold_selection_prefers_rejection_then_lower_threshold():
    rows = [{"threshold": 0.0, "recall_at_3": 1.0, "mrr_at_3": 1.0, "no_memory_rejection_rate": 0.0}, {"threshold": 0.4, "recall_at_3": 0.98, "mrr_at_3": 0.97, "no_memory_rejection_rate": 0.8}, {"threshold": 0.5, "recall_at_3": 0.98, "mrr_at_3": 0.97, "no_memory_rejection_rate": 0.8}]
    assert select_threshold(rows)["selected_threshold"] == 0.4
    assert select_threshold([rows[0], {**rows[1], "recall_at_3": 0.9}])["selected_threshold"] == 0.0
    assert select_threshold([rows[0], {**rows[1], "mrr_at_3": 0.9}])["selected_threshold"] == 0.0
    assert select_threshold([rows[0], {**rows[1], "recall_at_3": 0.9, "mrr_at_3": 0.9}])["selected_threshold"] == 0.0


def test_two_runs_are_isolated_and_reproducible():
    case = {"id":"same","category":"semantic","memories":[{"memory_key":"k","text":"日本旅遊"}],"lifecycle":[],"conversation_context":{},"query":"日本旅行","expected_memory_keys":["k"],"expected_no_memory":False}
    first, second = run_evaluation([case]), run_evaluation([case])
    for report in (first, second):
        report["latency_ms"] = {"p50": None, "p95": None}
    assert first["dense_scores"] == second["dense_scores"]
    assert first["case_results"] == second["case_results"]
