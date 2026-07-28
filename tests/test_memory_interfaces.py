import pytest

from pet_harness.memory.base_hybrid_index import BaseHybridIndex
from pet_harness.memory.base_memory_store import BaseMemoryStore, NullMemoryStore
from pet_harness.memory.memory_extractor import BaseMemoryExtractor
from pet_harness.memory.query_rewriter import BaseQueryRewriter
from pet_harness.memory.sparse_encoder import BaseSparseEncoder


@pytest.mark.parametrize(
    "interface", [BaseSparseEncoder, BaseQueryRewriter, BaseMemoryExtractor, BaseHybridIndex]
)
def test_memory_extension_points_are_abstract(interface):
    with pytest.raises(TypeError):
        interface()


def test_hybrid_index_is_separate_from_memory_store():
    """index()/search() 刻意不在 BaseMemoryStore 上:QdrantMemoryStore 的舊 collection
    沒有 sparse vector 也沒有 payload schema,結構上無法索引 Memory Item。"""
    assert not issubclass(BaseHybridIndex, BaseMemoryStore)
    assert not hasattr(NullMemoryStore, "index")
    assert not hasattr(NullMemoryStore, "search")
