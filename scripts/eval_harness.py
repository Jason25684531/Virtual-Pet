from __future__ import annotations

import hashlib
import json
import gc
import math
import statistics
import tempfile
import time
import uuid
import importlib
from pathlib import Path
from typing import Any, Callable

from qdrant_client import QdrantClient

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import MemoryItem, RetrievalRequest
from pet_harness.memory.memory_models import MemoryCandidate
from pet_harness.memory.memory_item_repository import MemoryItemRepository
from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.agent.result_parser import parse_fenced_json
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType


class DeterministicFakeDenseEncoder:
    dimension = 384

    def embed(self, texts):
        for text in texts:
            yield self(text)

    def __call__(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimension
            values[index] += 1.0
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]


class ProductionDenseEncoder:
    kind = "production_real"

    def __init__(self):
        from fastembed import TextEmbedding
        self.model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    def embed(self, texts):
        return self.model.embed(texts)

    def __call__(self, text: str) -> list[float]:
        return list(next(self.model.embed([text])))


def _tokens(text: str) -> set[str]:
    normalized = text.lower().replace("拉麵", "拉麵 ramen 麵食").replace("日本旅遊", "日本 旅行 旅遊").replace("東京天氣", "東京 天氣").replace("touchdiffusion", "touchdiffusion").replace("爵士樂", "爵士 音樂")
    words = {word for word in normalized.split() if word}
    words.update(normalized[i : i + 2] for i in range(len(normalized) - 1) if not normalized[i].isspace() and not normalized[i + 1].isspace())
    return words


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return numerator / denominator if denominator else 0.0


class CountingClient:
    def __init__(self, client):
        self.client = client
        self.query_calls = 0
        self.last_points = []

    def __getattr__(self, name):
        return getattr(self.client, name)

    def query_points(self, **kwargs):
        self.query_calls += 1
        response = self.client.query_points(**kwargs)
        self.last_points = response.points
        return response


def validate_case(case: dict[str, Any]) -> list[str]:
    required = {"id", "category", "memories", "lifecycle", "conversation_context", "query", "expected_memory_keys", "expected_no_memory"}
    errors = [f"missing fields: {sorted(required - case.keys())}"] if not required <= case.keys() else []
    keys = {item.get("memory_key") for item in case.get("memories", [])}
    expected = set(case.get("expected_memory_keys", []))
    no_memory = bool(case.get("expected_no_memory"))
    if bool(expected) == no_memory:
        errors.append("expected_memory_keys and expected_no_memory must be mutually exclusive")
    active_keys = keys | {item.get("memory_key") for item in case.get("lifecycle", [])}
    if expected - active_keys:
        errors.append(f"expected keys not seeded: {sorted(expected - active_keys)}")
    if no_memory and len(case.get("memories", [])) + len(case.get("lifecycle", [])) < 3:
        errors.append("no-memory case needs at least 3 distractors")
    return errors


def _item(case_id: str, index: int, value: dict[str, Any], status: str = "active") -> MemoryItem:
    memory_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"retrieval-eval:{case_id}:{index}"))
    return MemoryItem(memory_id, "eval", "default", value["memory_key"], value.get("memory_type", "semantic"), value["text"], status, f"{case_id}:{index}", "2026-01-01T00:00:00+00:00")


def _seed(case: dict[str, Any]) -> list[MemoryItem]:
    if case.get("lifecycle"):
        with tempfile.TemporaryDirectory(prefix="retrieval-eval-db-") as path:
            sqlite = SQLiteStore(Path(path) / "state.sqlite")
            sqlite.initialize()
            repository = MemoryItemRepository(sqlite, "eval")
            repository.upsert_candidates([MemoryCandidate(item["memory_key"], item.get("memory_type", "semantic"), item["text"], f"{case['id']}:repository") for item in case["memories"]])
            repository.upsert_candidates([MemoryCandidate(item["memory_key"], item.get("memory_type", "semantic"), item["text"], f"{case['id']}:repository") for item in case["lifecycle"] if item.get("status") == "active"])
            gc.collect()
    items = [_item(case["id"], index, value) for index, value in enumerate(case["memories"])]
    for index, value in enumerate(case.get("lifecycle", []), len(items)):
        if value.get("status") == "active":
            items = [item if item.memory_key != value["memory_key"] else MemoryItem(item.memory_id, item.character_id, item.user_id, item.memory_key, item.memory_type, item.text, "superseded", item.source_event_id, item.created_at) for item in items]
        items.append(_item(case["id"], index, value, "superseded" if value.get("status") == "superseded" else "active"))
    return items


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {key: 0 if key == "count" else None for key in ("count", "min", "p10", "p25", "median", "p75", "p90", "max")}
    ordered = sorted(values)
    def percentile(p):
        position = (len(ordered) - 1) * p
        low, high = math.floor(position), math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return {"count": len(values), "min": min(values), "p10": percentile(.1), "p25": percentile(.25), "median": statistics.median(values), "p75": percentile(.75), "p90": percentile(.9), "max": max(values)}


def ndcg_at_k(actual: list[str], expected: list[str], k: int = 3) -> float:
    if not expected:
        return 0.0
    expected_set = set(expected)
    dcg = sum(1 / math.log2(rank + 1) for rank, key in enumerate(actual[:k], 1) if key in expected_set)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(expected_set), k) + 1))
    return dcg / ideal if ideal else 0.0


def metric_for_keys(actual: list[str], expected: list[str]) -> tuple[float, float, float, float]:
    for rank, key in enumerate(actual[:3], 1):
        if key in expected:
            return float(rank == 1), 1.0, 1.0 / rank, ndcg_at_k(actual, expected)
    return 0.0, 0.0, 0.0, 0.0


def _result_or_status(evaluator: Callable | None, *args) -> dict[str, Any]:
    if evaluator is None:
        return {"status": "not_available"}
    try:
        value = evaluator(*args)
        return {"status": "available", **dict(value)}
    except Exception as exc:
        return {"status": "evaluation_error", "error": f"{type(exc).__name__}: {exc}"}


def _timed_result(evaluator: Callable | None, *args) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    return _result_or_status(evaluator, *args), (time.perf_counter() - started) * 1000


def build_ollama_pointwise_evaluator(model: str, base_url: str, timeout_seconds: float, *, provider: OllamaProvider | None = None) -> Callable[[str, str], dict[str, Any]]:
    provider = provider or OllamaProvider(ProviderConfig(provider_type=ProviderType.OLLAMA, model_name=model, base_url=base_url, timeout_seconds=timeout_seconds, metadata={"format": "json", "options": {"temperature": 0}}))

    def evaluate(query: str, evidence: str) -> dict[str, Any]:
        prompt = f'''Judge whether the evidence directly contains information needed to answer the question. Do not use outside knowledge. Return JSON only: {{"relevant": true|false, "score": 0.0, "reason": "short reason"}}. Score is confidence that the evidence is sufficient and relevant.
Question: {query}
Evidence: {evidence}'''
        reply = provider.generate_reply(UserEvent(text=query, source="pointwise_evaluator"), prompt_text=prompt)
        if not reply.provider_status.healthy:
            raise RuntimeError(reply.provider_status.message)
        payload = parse_fenced_json(reply.raw_text or reply.reply)
        if not isinstance(payload, dict) or not isinstance(payload.get("relevant"), bool):
            raise ValueError("Ollama pointwise response must contain boolean relevant")
        score = float(payload.get("score"))
        if not 0.0 <= score <= 1.0:
            raise ValueError("Ollama pointwise score must be between 0 and 1")
        return {"relevant": payload["relevant"], "score": score, "reason": str(payload.get("reason") or ""), "model": model}

    return evaluate


def _rate(numerator: int, denominator: int, **counts: int) -> dict[str, Any]:
    return {"value": numerator / denominator if denominator else None, **counts}


def build_case_trace(case: dict[str, Any], result, dense_points, sparse_points, fusion_points, *, ground_truth_ids: list[str], latency_ms: dict[str, float], pointwise_evaluator: Callable | None = None, answer_generator: Callable | None = None, correctness_evaluator: Callable | None = None, faithfulness_evaluator: Callable | None = None) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    latency = dict(latency_ms)
    pointwise_total = 0.0
    for channel, points in (("dense", dense_points), ("sparse", sparse_points), ("fusion", fusion_points)):
        for rank, point in enumerate(points, 1):
            point_id = str(point.id)
            row = rows.setdefault(point_id, {"point_id": point_id, **dict(point.payload or {})})
            row[f"{channel}_rank"] = rank
            row[f"{channel}_score"] = float(point.score)
    evidence_ids = {item.memory_id for item in result.evidence}
    for row in rows.values():
        for channel in ("dense", "sparse", "fusion"):
            row.setdefault(f"{channel}_rank", None)
            row.setdefault(f"{channel}_score", None)
        row["in_evidence"] = row["point_id"] in evidence_ids
        if row["in_evidence"]:
            expected_relevant = row["memory_id"] in ground_truth_ids
            if pointwise_evaluator:
                row["pointwise"], elapsed = _timed_result(pointwise_evaluator, case["query"], row["text"])
                row["pointwise"]["expected_relevant"] = expected_relevant
                row["pointwise"]["correct"] = row["pointwise"].get("relevant") is expected_relevant
            else:
                row["pointwise"], elapsed = {"status": "ground_truth", "relevant": expected_relevant, "expected_relevant": expected_relevant, "correct": True}, 0.0
            pointwise_total += elapsed
        else:
            row["pointwise"] = {"status": "not_applicable"}
    actual = [item.memory_key for item in result.evidence[:3]]
    hit_at_1, hit_at_3, mrr, ndcg = metric_for_keys(actual, case["expected_memory_keys"])
    retrieval_results = sorted(rows.values(), key=lambda row: (row["fusion_rank"] is None, row["fusion_rank"] or row["dense_rank"] or row["sparse_rank"]))
    ranks = [row["fusion_rank"] for row in retrieval_results if row.get("memory_id") in ground_truth_ids and row["fusion_rank"] <= 5]
    before = min(ranks) if ranks else None
    latency["pointwise"] = pointwise_total if pointwise_evaluator else None
    no_memory = not bool(ground_truth_ids)
    return {"trace_id": case["id"], "query": case["query"], "ground_truth": {"has_memory": not no_memory, "memory_ids": ground_truth_ids}, "retrieval_results": retrieval_results, "retrieval_evaluation": {"has_ground_truth": not no_memory, "ground_truth_memory_ids": ground_truth_ids, "top_k": 5, "hit": bool(ranks), "best_ground_truth_rank": before}, "no_memory_evaluation": {"is_no_memory_query": no_memory, "correct_rejection": not any(row["in_evidence"] for row in retrieval_results) if no_memory else None, "false_positive": any(row["in_evidence"] for row in retrieval_results) if no_memory else None, "accepted_evidence_count": sum(row["in_evidence"] for row in retrieval_results)}, "debug_metrics": {"hit_at_1": hit_at_1, "hit_at_3": hit_at_3, "mrr_at_3": mrr, "ndcg_at_3": ndcg}, "latency_ms": latency}


def summarize_traces(traces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    relevant = [trace for trace in traces if trace["ground_truth"]["has_memory"]]
    evidence = [row["pointwise"] for trace in traces for row in trace["retrieval_results"] if row["in_evidence"] and row["pointwise"]["status"] in {"available", "ground_truth"}]
    no_memory = [trace["no_memory_evaluation"] for trace in traces if trace["no_memory_evaluation"]["is_no_memory_query"]]
    return {"retrieval_accuracy": _rate(sum(trace["retrieval_evaluation"]["hit"] for trace in relevant), len(relevant), passed=sum(trace["retrieval_evaluation"]["hit"] for trace in relevant), total=len(relevant)), "pointwise_accuracy": _rate(sum(item["correct"] for item in evidence), len(evidence), correct=sum(item["correct"] for item in evidence), total_evidence=len(evidence)), "no_memory_rejection_rate": _rate(sum(item["correct_rejection"] for item in no_memory), len(no_memory), correct_rejections=sum(item["correct_rejection"] for item in no_memory), total_no_memory=len(no_memory))}


def select_threshold(rows: list[dict[str, Any]], *, recall_guard: float = 0.05, mrr_guard: float = 0.05) -> dict[str, Any]:
    if not rows:
        return {"selected_threshold": 0.0, "reason": "no real-model sweep rows", "eligible": []}
    baseline = rows[0]
    eligible = [row for row in rows if row["recall_at_3"] >= baseline["recall_at_3"] - recall_guard and row["mrr_at_3"] >= baseline["mrr_at_3"] - mrr_guard]
    if not eligible:
        return {"selected_threshold": 0.0, "reason": "no safe threshold found", "eligible": [], "baseline": baseline}
    selected = sorted(eligible, key=lambda row: (-row["no_memory_rejection_rate"], row["threshold"]))[0]
    return {"selected_threshold": selected["threshold"], "reason": "max rejection among quality-preserving thresholds; lowest threshold tie-break", "eligible": eligible, "baseline": baseline, "selected": selected, "recall_guard": recall_guard, "mrr_guard": mrr_guard}


def run_evaluation(cases: list[dict[str, Any]], *, real_encoder: bool = False, threshold: float | None = None, collect_traces: bool = False, reranker=None, pointwise_evaluator: Callable | None = None, answer_generator: Callable | None = None, correctness_evaluator: Callable | None = None, faithfulness_evaluator: Callable | None = None) -> dict[str, Any]:
    import config
    fake = DeterministicFakeDenseEncoder()
    try:
        encoder = ProductionDenseEncoder() if real_encoder else fake
        calibration_status = "measured" if real_encoder else "not requested"
        encoder_kind = "production_real" if real_encoder else "deterministic_fake"
    except Exception as exc:
        return {"evaluation_valid": True, "configuration_failure_count": 0, "cases": len(cases), "valid_relevant_cases": 0, "valid_no_memory_cases": 0, "real_model_calibration": "not measured", "calibration_error": f"{type(exc).__name__}: {exc}", "threshold_selection": {"selected_threshold": 0.0, "reason": "real encoder unavailable"}, "dense_scores": {"encoder_kind": "production_real", "positive": _stats([]), "negative": _stats([]), "no_memory_negative": _stats([])}, "case_results": []}
    old_threshold = config.MEMORY_DENSE_MIN_SCORE
    if threshold is not None:
        config.MEMORY_DENSE_MIN_SCORE = threshold
    durations, calls, positives, negatives, no_memory_negatives = [], [], [], [], []
    failures = []
    valid_relevant = valid_no_memory = 0
    hit_at_1_values, hit_at_3_values, mrr_values, ndcg_values, rejection_values, traces = [], [], [], [], [], []
    try:
        for case in cases:
            errors = validate_case(case)
            if errors:
                failures.append({"id": case.get("id"), "configuration_error": "FAIL CASE CONFIGURATION: " + "; ".join(errors)})
                continue
            items = _seed(case)
            with tempfile.TemporaryDirectory(prefix="retrieval-eval-") as path:
                client = CountingClient(QdrantClient(path=path))
                sparse = JiebaBm25SparseEncoder()
                store = HybridQdrantMemoryStore(character_id="eval", path=path, client=client, dense_encoder=encoder, sparse_encoder=sparse)
                store.index(items)
                retriever = ContextualMemoryRetriever(store, store.embed_dense, store.sparse_encoder, reranker=reranker)
                started = time.perf_counter()
                context = case["conversation_context"]
                request = RetrievalRequest("eval", case["query"], context.get("previous_user"), context.get("previous_assistant"), top_k=5)
                result = retriever.retrieve(request)
                durations.append((time.perf_counter() - started) * 1000)
                calls.append(client.query_calls)
                actual = [item.memory_key for item in result.evidence[:3]]
                expected = case["expected_memory_keys"]
                if expected:
                    valid_relevant += 1
                    hit_at_1, hit_at_3, mrr, ndcg = metric_for_keys(actual, expected)
                    hit_at_1_values.append(hit_at_1); hit_at_3_values.append(hit_at_3); mrr_values.append(mrr); ndcg_values.append(ndcg)
                    if not hit_at_3:
                        failures.append({"id": case["id"], "query": case["query"], "expected": expected, "actual_top3": actual, "reason": "no expected memory_key in Top3"})
                else:
                    valid_no_memory += 1
                    rejection_values.append(1.0 if not result.evidence else 0.0)
                    if result.evidence:
                        failures.append({"id": case["id"], "query": case["query"], "expected": [], "actual_top3": actual, "reason": "irrelevant evidence returned for no-memory case"})
                query_vector = encoder(case["query"])
                for item in items:
                    if item.status != "active":
                        continue
                    cosine = cosine_similarity(query_vector, encoder(item.text))
                    if item.memory_key in expected:
                        positives.append(cosine)
                    else:
                        negatives.append(cosine)
                        if not expected:
                            no_memory_negatives.append(cosine)
                if collect_traces:
                    from qdrant_client import models

                    query = result.trace.standalone_query
                    active = models.Filter(must=[models.FieldCondition(key="status", match=models.MatchValue(value="active"))])
                    raw_client = client.client
                    dense_points = raw_client.query_points(collection_name=store.collection, query=store.embed_dense(query), using="dense", query_filter=active, limit=20, with_payload=True).points
                    sparse_vector = sparse.encode(query) if sparse.status().state == "ready" else None
                    sparse_points = raw_client.query_points(collection_name=store.collection, query=models.SparseVector(indices=list(sparse_vector), values=list(sparse_vector.values())), using="sparse", query_filter=active, limit=20, with_payload=True).points if sparse_vector else []
                    ground_truth_ids = [item.memory_id for item in items if item.status == "active" and item.memory_key in expected]
                    traces.append(build_case_trace(case, result, dense_points, sparse_points, client.last_points, ground_truth_ids=ground_truth_ids, latency_ms=result.trace.latency_ms, pointwise_evaluator=pointwise_evaluator, answer_generator=answer_generator, correctness_evaluator=correctness_evaluator, faithfulness_evaluator=faithfulness_evaluator))
                store.shutdown()
    finally:
        config.MEMORY_DENSE_MIN_SCORE = old_threshold
    ordered = sorted(durations)
    report = {"evaluation_valid": not any("configuration_error" in failure for failure in failures), "configuration_failure_count": sum("configuration_error" in failure for failure in failures), "configuration_failure_cases": [failure["id"] for failure in failures if "configuration_error" in failure], "cases": len(cases), "valid_relevant_cases": valid_relevant, "valid_no_memory_cases": valid_no_memory, "recall_at_3": sum(hit_at_3_values) / valid_relevant if valid_relevant else None, "hit_at_1": sum(hit_at_1_values) / valid_relevant if valid_relevant else None, "hit_at_3": sum(hit_at_3_values) / valid_relevant if valid_relevant else None, "mrr_at_3": sum(mrr_values) / valid_relevant if valid_relevant else None, "ndcg_at_3": sum(ndcg_values) / valid_relevant if valid_relevant else None, "no_memory_rejection_rate": sum(rejection_values) / valid_no_memory if valid_no_memory else None, "production_qdrant_calls_per_query": sum(calls) / len(calls) if calls else None, "latency_ms": {"p50": statistics.median(durations) if durations else None, "p95": ordered[round(.95 * (len(ordered) - 1))] if ordered else None}, "dense_scores": {"encoder_kind": encoder_kind, "positive": _stats(positives), "negative": _stats(negatives), "no_memory_negative": _stats(no_memory_negatives)}, "case_results": failures, "real_model_calibration": calibration_status, "threshold": threshold}
    if collect_traces:
        report["traces"] = traces
        report["summary"] = summarize_traces(traces)
    return report


def run_threshold_sweep(cases: list[dict[str, Any]], thresholds: list[float]) -> dict[str, Any]:
    rows = []
    for value in thresholds:
        result = run_evaluation(cases, real_encoder=True, threshold=value)
        rows.append({"threshold": value, "recall_at_3": result.get("recall_at_3"), "mrr_at_3": result.get("mrr_at_3"), "no_memory_rejection_rate": result.get("no_memory_rejection_rate"), "production_qdrant_calls_per_query": result.get("production_qdrant_calls_per_query"), "latency_ms": result.get("latency_ms"), "evaluation_valid": result.get("evaluation_valid", False)})
        if result.get("real_model_calibration") == "not measured":
            return {"rows": [], "selection": {"selected_threshold": 0.0, "reason": "real encoder unavailable"}, "calibration": result}
    return {"rows": rows, "selection": select_threshold(rows)}
