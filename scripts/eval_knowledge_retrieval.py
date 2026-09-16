"""量測共用知識庫的命中率、誤命中率與延遲。

用法：python scripts/eval_knowledge_retrieval.py
前提：已先跑過 scripts/ingest_knowledge.py。
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from pet_harness.knowledge.gate import load_keywords, needs_retrieval
from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
from pet_harness.memory.fastembed_reranker import FastembedReranker
from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
from pet_harness.memory.memory_models import RetrievalRequest


def _print_score_range(label: str, scores: list[float]) -> None:
    if not scores:
        return
    sorted_scores = sorted(scores)
    print(f"  {label}：min={sorted_scores[0]:.3f} median={statistics.median(sorted_scores):.3f} max={sorted_scores[-1]:.3f}")


def main() -> int:
    data = json.loads((Path(__file__).resolve().parents[1] / "tests" / "data" / "knowledge_eval_set.json").read_text(encoding="utf-8"))

    store = HybridQdrantMemoryStore(character_id="shared", path=str(config.KNOWLEDGE_QDRANT_PATH), collection=config.KNOWLEDGE_COLLECTION, dense_min_score=config.KNOWLEDGE_DENSE_MIN_SCORE)
    if store.status().state != "ready":
        print(f"向量庫未就緒：{store.status().reason}；請先執行 scripts/ingest_knowledge.py")
        return 1
    retriever = ContextualMemoryRetriever(
        store,
        store.embed_dense,
        store.sparse_encoder,
        reranker=(
            FastembedReranker(threshold=config.KNOWLEDGE_RERANK_MIN_SCORE)
            if config.KNOWLEDGE_RERANK_ENABLED else None
        ),
    )
    keywords = load_keywords(config.KNOWLEDGE_KEYWORDS_PATH)

    print(
        f"知識門檻：dense={config.KNOWLEDGE_DENSE_MIN_SCORE} "
        f"rerank={config.KNOWLEDGE_RERANK_MIN_SCORE} "
        f"rerank_enabled={config.KNOWLEDGE_RERANK_ENABLED}"
    )

    hit_at_3 = hit_at_5 = 0
    latencies_ms: list[float] = []
    game_rerank_scores: list[float] = []
    expected_rerank_scores: list[float] = []
    misses = []
    for case in data["retrieval_cases"]:
        t0 = time.perf_counter()
        result = retriever.retrieve(RetrievalRequest("eval", case["query"], top_k=config.KNOWLEDGE_RETRIEVE_K))
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        game_rerank_scores.extend(float(score) for score in result.trace.rerank_scores.values())
        expected_item = next(
            (item for item in result.evidence if item.memory_key == case["expected_chunk_id"]),
            None,
        )
        if expected_item is not None and expected_item.memory_id in result.trace.rerank_scores:
            expected_rerank_scores.append(float(result.trace.rerank_scores[expected_item.memory_id]))
        ids = [item.memory_key for item in result.evidence]
        if case["expected_chunk_id"] in ids[:3]:
            hit_at_3 += 1
        if case["expected_chunk_id"] in ids:
            hit_at_5 += 1
        else:
            misses.append((case["query"], case["expected_chunk_id"], ids))

    total = len(data["retrieval_cases"])
    print(f"檢索案例：{total}")
    print(f"  hit@3 = {hit_at_3}/{total} ({hit_at_3/total:.0%})")
    print(f"  hit@5 = {hit_at_5}/{total} ({hit_at_5/total:.0%})")
    if misses:
        print("  未命中：")
        for query, expected, got in misses:
            print(f"    - {query!r} 預期 {expected}，實得 {got}")
    if latencies_ms:
        sorted_ms = sorted(latencies_ms)
        p50 = statistics.median(sorted_ms)
        p95 = sorted_ms[min(len(sorted_ms) - 1, int(len(sorted_ms) * 0.95))]
        print(f"  retrieve() 延遲：p50={p50:.0f}ms p95={p95:.0f}ms（暖機後，不含首次模型載入）")
    _print_score_range("遊戲題 rerank 分數範圍", game_rerank_scores)
    _print_score_range(f"已召回 expected chunk 分數（n={len(expected_rerank_scores)}）", expected_rerank_scores)

    gate_false_positive = 0
    evidence_false_positive = 0
    no_hit_scores: list[float] = []
    for text in data["no_hit_cases"]:
        if needs_retrieval(text, keywords):
            gate_false_positive += 1
            print(f"  閘門誤觸發：{text!r}")
        # 直接送入檢索，量測「即使查詢沒有關鍵詞，候選是否能穿過相關性閘門」；
        # 這和上面的輕量 gate 誤觸發是兩個不同指標。
        result = retriever.retrieve(RetrievalRequest("eval", text, top_k=config.KNOWLEDGE_RETRIEVE_K))
        if result.evidence:
            evidence_false_positive += 1
            print(f"  evidence 誤命中：{text!r} -> {[item.memory_key for item in result.evidence]}")
        no_hit_scores.extend(float(score) for score in result.trace.rerank_scores.values())
    no_hit_total = len(data["no_hit_cases"])
    print(f"閒聊案例：{no_hit_total}，閘門誤觸發 {gate_false_positive}/{no_hit_total} ({gate_false_positive/no_hit_total:.0%})")
    print(f"閒聊 evidence 誤命中：{evidence_false_positive}/{no_hit_total} ({evidence_false_positive/no_hit_total:.0%})")
    _print_score_range("閒聊 rerank 分數範圍", no_hit_scores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
