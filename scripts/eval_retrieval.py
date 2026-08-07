from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import RetrievalRequest


def metrics(rankings, cases, durations):
    if len(rankings) != len(cases) or len(durations) != len(cases):
        raise ValueError("evaluation output does not match the case count")
    found = [any(any(fact in hit for hit in ranking) for fact in case["expected_facts"]) if case["expected_facts"] else not ranking for ranking, case in zip(rankings, cases)]
    reciprocal = [next((1 / (i + 1) for i, hit in enumerate(ranking) if any(fact in hit for fact in case["expected_facts"])), 0) for ranking, case in zip(rankings, cases)]
    ordered = sorted(durations)
    ndcg = [next((1 / __import__("math").log2(i + 2) for i, hit in enumerate(ranking) if any(fact in hit for fact in case["expected_facts"])), 0) for ranking, case in zip(rankings, cases)]
    return {"recall_at_5": sum(found) / len(cases), "mrr": sum(reciprocal) / len(cases), "ndcg_at_5": sum(ndcg) / len(cases), "empty_retrieval_rate": sum(not x for x in rankings) / len(cases), "p50_ms": statistics.median(durations), "p95_ms": ordered[round(.95 * (len(ordered) - 1))]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", default="tests/data/retrieval_eval_set.json")
    args = parser.parse_args()
    cases = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    hybrid = HybridQdrantMemoryStore(character_id="miku", path="data/characters/miku/qdrant")
    retriever = ContextualMemoryRetriever(hybrid, hybrid.embed_dense, hybrid.sparse_encoder)
    rankings, durations, cross_character_leakage, wrong_assistant_evidence = [], [], 0, 0
    for case in cases:
        started = time.perf_counter()
        result = retriever.retrieve(RetrievalRequest("miku", case["current_turn"], case.get("previous_user"), case.get("previous_assistant")))
        durations.append((time.perf_counter() - started) * 1000)
        rankings.append([item.text for item in result.evidence])
        cross_character_leakage += sum(item.character_id != "miku" for item in result.evidence)
        wrong_assistant_evidence += sum(bool(case.get("previous_assistant")) and item.text == case["previous_assistant"] for item in result.evidence)
    print(json.dumps({"hybrid": {"store_status": hybrid.status().state, **metrics(rankings, cases, durations), "cross_character_leakage": cross_character_leakage, "wrong_assistant_evidence": wrong_assistant_evidence}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
