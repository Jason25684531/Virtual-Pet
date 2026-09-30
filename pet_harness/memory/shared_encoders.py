from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def get_dense_encoder():
    """行程內單例：384 維多語 dense encoder。角色記憶庫（每角色一個 collection）與
    共用知識庫都注入同一個實例，避免每次切換角色重新載入一次模型
    （見 openspec/changes/shared-knowledge-rag/design.md D8）。"""
    from fastembed import TextEmbedding

    return TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


@functools.lru_cache(maxsize=1)
def get_sparse_encoder():
    """行程內單例：jieba + BM25 sparse encoder，理由同 get_dense_encoder。"""
    from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder

    return JiebaBm25SparseEncoder()
