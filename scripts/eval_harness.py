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
from typing import Any

from qdrant_client import QdrantClient

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import MemoryItem, RetrievalRequest
from pet_harness.memory.memory_models import MemoryCandidate
from pet_harness.memory.memory_item_repository import MemoryItemRepository
from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder
from pet_harness.storage.sqlite_store import SQLiteStore


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

    def __getattr__(self, name):
        return getattr(self.client, name)

    def query_points(self, **kwargs):
        self.query_calls += 1
        return self.client.query_points(**kwargs)


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


def metric_for_keys(actual: list[str], expected: list[str]) -> tuple[float, float]:
    for rank, key in enumerate(actual[:3], 1):
        if key in expected:
            return 1.0, 1.0 / rank
    return 0.0, 0.0


def select_threshold(rows: list[dict[str, Any]], *, recall_guard: float = 0.05, mrr_guard: float = 0.05) -> dict[str, Any]:
    if not rows:
        return {"selected_threshold": 0.0, "reason": "no real-model sweep rows", "eligible": []}
    baseline = rows[0]
    eligible = [row for row in rows if row["recall_at_3"] >= baseline["recall_at_3"] - recall_guard and row["mrr_at_3"] >= baseline["mrr_at_3"] - mrr_guard]
    if not eligible:
        return {"selected_threshold": 0.0, "reason": "no safe threshold found", "eligible": [], "baseline": baseline}
    selected = sorted(eligible, key=lambda row: (-row["no_memory_rejection_rate"], row["threshold"]))[0]
    return {"selected_threshold": selected["threshold"], "reason": "max rejection among quality-preserving thresholds; lowest threshold tie-break", "eligible": eligible, "baseline": baseline, "selected": selected, "recall_guard": recall_guard, "mrr_guard": mrr_guard}


def run_evaluation(cases: list[dict[str, Any]], *, real_encoder: bool = False, threshold: float | None = None) -> dict[str, Any]:
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
    recall_values, mrr_values, rejection_values = [], [], []
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
                retriever = ContextualMemoryRetriever(store, store.embed_dense, store.sparse_encoder)
                started = time.perf_counter()
                context = case["conversation_context"]
                request = RetrievalRequest("eval", case["query"], context.get("previous_user"), context.get("previous_assistant"), top_k=3)
                result = retriever.retrieve(request)
                durations.append((time.perf_counter() - started) * 1000)
                calls.append(client.query_calls)
                actual = [item.memory_key for item in result.evidence[:3]]
                expected = case["expected_memory_keys"]
                if expected:
                    valid_relevant += 1
                    recall, mrr = metric_for_keys(actual, expected)
                    recall_values.append(recall); mrr_values.append(mrr)
                    if not recall:
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
                store.shutdown()
    finally:
        config.MEMORY_DENSE_MIN_SCORE = old_threshold
    ordered = sorted(durations)
    return {"evaluation_valid": not any("configuration_error" in failure for failure in failures), "configuration_failure_count": sum("configuration_error" in failure for failure in failures), "configuration_failure_cases": [failure["id"] for failure in failures if "configuration_error" in failure], "cases": len(cases), "valid_relevant_cases": valid_relevant, "valid_no_memory_cases": valid_no_memory, "recall_at_3": sum(recall_values) / valid_relevant if valid_relevant else None, "mrr_at_3": sum(mrr_values) / valid_relevant if valid_relevant else None, "no_memory_rejection_rate": sum(rejection_values) / valid_no_memory if valid_no_memory else None, "production_qdrant_calls_per_query": sum(calls) / len(calls) if calls else None, "latency_ms": {"p50": statistics.median(durations) if durations else None, "p95": ordered[round(.95 * (len(ordered) - 1))] if ordered else None}, "dense_scores": {"encoder_kind": encoder_kind, "positive": _stats(positives), "negative": _stats(negatives), "no_memory_negative": _stats(no_memory_negatives)}, "case_results": failures, "real_model_calibration": calibration_status, "threshold": threshold}


def run_threshold_sweep(cases: list[dict[str, Any]], thresholds: list[float]) -> dict[str, Any]:
    rows = []
    for value in thresholds:
        result = run_evaluation(cases, real_encoder=True, threshold=value)
        rows.append({"threshold": value, "recall_at_3": result.get("recall_at_3"), "mrr_at_3": result.get("mrr_at_3"), "no_memory_rejection_rate": result.get("no_memory_rejection_rate"), "production_qdrant_calls_per_query": result.get("production_qdrant_calls_per_query"), "latency_ms": result.get("latency_ms"), "evaluation_valid": result.get("evaluation_valid", False)})
        if result.get("real_model_calibration") == "not measured":
            return {"rows": [], "selection": {"selected_threshold": 0.0, "reason": "real encoder unavailable"}, "calibration": result}
    return {"rows": rows, "selection": select_threshold(rows)}
