from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def get_knowledge_retriever():
    """行程內單例：共用知識庫唯讀，只建立一次，切換角色時重用不重建——
    Qdrant 本機模式會鎖住資料夾，重建即衝突（design D2/D8）。
    KNOWLEDGE_RAG_ENABLED=False 時回傳 None，呼叫端據此完全跳過知識檢索。
    """
    import config

    if not config.KNOWLEDGE_RAG_ENABLED:
        return None
    from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
    from pet_harness.memory.fastembed_reranker import FastembedReranker
    from pet_harness.memory.hybrid_qdrant_memory_store import HybridQdrantMemoryStore
    from pet_harness.memory.shared_encoders import get_dense_encoder, get_sparse_encoder

    store = HybridQdrantMemoryStore(
        character_id="shared",
        path=config.KNOWLEDGE_QDRANT_PATH,
        collection=config.KNOWLEDGE_COLLECTION,
        dense_encoder=get_dense_encoder(),
        sparse_encoder=get_sparse_encoder(),
        dense_min_score=config.KNOWLEDGE_DENSE_MIN_SCORE,
    )
    return ContextualMemoryRetriever(
        store, store.embed_dense, store.sparse_encoder,
        reranker=FastembedReranker() if config.KNOWLEDGE_RERANK_ENABLED else None,
    )


@functools.lru_cache(maxsize=1)
def get_knowledge_keywords() -> frozenset[str]:
    """RAG 前的輕量閘門用詞表；ingest_knowledge.py 灌庫時寫入，這裡只讀。"""
    import config

    if not config.KNOWLEDGE_RAG_ENABLED:
        return frozenset()
    from pet_harness.knowledge.gate import load_keywords

    return load_keywords(config.KNOWLEDGE_KEYWORDS_PATH)
