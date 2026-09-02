import json
import math

from scripts.eval_backfill import build_rows
from scripts.eval_harness import build_ollama_pointwise_evaluator, ndcg_at_k, run_evaluation, summarize_traces
from scripts.eval_retrieval import gate_report, quality_report


def test_trace_contains_all_channels_and_exact_ndcg(tmp_path):
    cases = [
        {"id": "hit", "category": "semantic", "memories": [{"memory_key": "target", "text": "target text"}], "lifecycle": [], "conversation_context": {}, "query": "target", "expected_memory_keys": ["target"], "expected_no_memory": False},
        {"id": "miss", "category": "no-memory", "memories": [{"memory_key": str(i), "text": f"distractor {i}"} for i in range(3)], "lifecycle": [], "conversation_context": {}, "query": "unrelated", "expected_memory_keys": [], "expected_no_memory": True},
    ]
    report = run_evaluation(cases, collect_traces=True)
    path = tmp_path / "trace.jsonl"
    path.write_text("".join(json.dumps(trace) + "\n" for trace in report["traces"]), encoding="utf-8")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(cases)
    assert lines[0]["rerank_status"] == "not_available"
    assert lines[0]["rewrite_status"] == "not_available"
    assert {"dense_rank", "dense_score", "sparse_rank", "sparse_score", "fusion_rank", "fusion_score", "in_evidence"} <= lines[0]["retrieval_results"][0].keys()
    assert lines[0]["retrieval_results"][0]["pointwise"] == {"status": "ground_truth", "relevant": True, "expected_relevant": True, "correct": True}
    assert report["production_qdrant_calls_per_query"] == 1.0
    assert ndcg_at_k(["target"], ["target"]) == 1.0
    assert ndcg_at_k(["x", "y", "target"], ["target"]) == 1 / math.log2(4)


def test_business_kpis_exclude_no_memory_and_preserve_unavailable_states():
    traces = [
        {"ground_truth": {"has_memory": True}, "retrieval_evaluation": {"hit": True}, "retrieval_results": [{"in_evidence": True, "pointwise": {"status": "available", "correct": True}}], "no_memory_evaluation": {"is_no_memory_query": False}},
        {"ground_truth": {"has_memory": True}, "retrieval_evaluation": {"hit": False}, "retrieval_results": [{"in_evidence": True, "pointwise": {"status": "available", "correct": False}}], "no_memory_evaluation": {"is_no_memory_query": False}},
        {"ground_truth": {"has_memory": False}, "retrieval_evaluation": {"hit": False}, "retrieval_results": [], "no_memory_evaluation": {"is_no_memory_query": True, "correct_rejection": True}},
    ]
    summary = summarize_traces(traces)
    assert summary["retrieval_accuracy"] == {"value": 0.5, "passed": 1, "total": 2}
    assert summary["pointwise_accuracy"]["value"] == 0.5
    assert summary["no_memory_rejection_rate"]["value"] == 1.0


def test_pointwise_evaluator_is_checked_against_dataset_ground_truth():
    case = {"id": "answer", "category": "semantic", "memories": [{"memory_key": "target", "text": "target fact"}, {"memory_key": "other", "text": "other fact"}], "lifecycle": [], "conversation_context": {}, "query": "target", "expected_memory_keys": ["target"], "expected_no_memory": False, "reference_answer": "target fact"}
    report = run_evaluation([case], collect_traces=True, pointwise_evaluator=lambda _q, _text: {"relevant": False})
    trace = report["traces"][0]
    target = next(row for row in trace["retrieval_results"] if row["memory_key"] == "target")
    assert target["pointwise"]["expected_relevant"] is True
    assert target["pointwise"]["correct"] is False
    assert report["summary"]["pointwise_accuracy"]["value"] == 0.0


def test_quality_report_contains_only_the_six_business_kpis():
    payload, text = quality_report({"retrieval_accuracy": {"value": 1.0}, "pointwise_accuracy": {"value": 0.5}, "no_memory_rejection_rate": {"value": 0.5}})
    assert payload == {"rag_quality": {"retrieval_accuracy": 1.0, "pointwise_accuracy": 0.5, "no_memory_rejection_rate": 0.5}}
    lines = text.splitlines()
    assert lines[:2] == ["RAG Quality", "─" * 24]
    assert [line.split()[0] for line in lines[2:]] == ["Retrieval", "Pointwise", "No-Memory"]
    assert [line.split()[-1] for line in lines[2:]] == ["100.00%", "50.00%", "50.00%"]


def test_ollama_pointwise_evaluator_parses_structured_score():
    class Provider:
        def generate_reply(self, *_args, **_kwargs):
            return type("Reply", (), {"raw_text": '{"relevant": true, "score": 0.75, "reason": "direct"}', "reply": "", "provider_status": type("Status", (), {"healthy": True, "message": "ready"})()})()

    result = build_ollama_pointwise_evaluator("gemma3:12b-it-qat", "http://localhost:11434", 1, provider=Provider())("question", "evidence")
    assert result == {"relevant": True, "score": 0.75, "reason": "direct", "model": "gemma3:12b-it-qat"}


def test_gate_report_checks_red_line_and_ignores_follow_up_pointwise():
    def trace(trace_id, *, follow_up=False, rejected=None, correct=True):
        if rejected is not None:
            return {"trace_id": trace_id, "sheet_ref": "C1", "category": "no-memory", "ground_truth": {"has_memory": False}, "no_memory_evaluation": {"is_no_memory_query": True, "correct_rejection": rejected}}
        return {"trace_id": trace_id, "category": "follow-up" if follow_up else "semantic", "ground_truth": {"has_memory": True}, "retrieval_evaluation": {"hit": True}, "retrieval_results": [{"in_evidence": True, "pointwise": {"status": "ground_truth", "correct": correct}}]}

    traces = [trace("hit"), trace("follow", follow_up=True, correct=False), trace("c1", rejected=False)]
    summary = summarize_traces(traces)
    report = gate_report(summary, traces)
    assert report["conditions"]["pointwise"]["total"] == 1
    assert report["conditions"]["no_memory"]["red_line"]["passed"] is False
    assert report["passed"] is False


def test_backfill_maps_follow_up_and_xfail(tmp_path):
    traces = [{"trace_id": "a2", "sheet_ref": "A2", "category": "follow-up", "ground_truth": {"has_memory": True}, "retrieval_evaluation": {"hit": True}, "retrieval_results": [{"in_evidence": True, "pointwise": {"correct": False}}]}]
    xml = tmp_path / "write.xml"
    xml.write_text('<testsuite><testcase name="test_sheet_c3_sensitive"><skipped type="pytest.xfail" message="敏感資訊過濾未實作">敏感資訊過濾未實作</skipped></testcase></testsuite>', encoding="utf-8")
    rows, skipped = build_rows(traces, xml)
    assert skipped == 0
    assert {row["sheet_ref"]: row["判定"] for row in rows} == {"A2": "合格", "C3": "不合格"}
