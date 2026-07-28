import json
import subprocess
import sys

from pet_harness.memory.base_memory_store import MemoryStoreStatus
from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder


def test_jieba_bm25_uses_fastembed_indices_and_term_frequency():
    code = """import json
from pet_harness.memory.sparse_encoder import JiebaBm25SparseEncoder
encoder = JiebaBm25SparseEncoder()
sentence = encoder.encode('我喜歡吃蘋果')
repeated = encoder.encode('蘋果 蘋果 蘋果')
single = encoder.encode('蘋果')
print(json.dumps([encoder.status().state, len(sentence), set(repeated) == set(single), repeated != single]))
"""
    output = subprocess.check_output([sys.executable, "-c", code], text=True)
    state, size, same_indices, values_differ = json.loads(output.splitlines()[-1])
    assert state == "ready"
    assert size >= 3
    assert same_indices and values_differ


def test_sparse_encoder_degraded_mode_disables_sparse_signal():
    encoder = JiebaBm25SparseEncoder.__new__(JiebaBm25SparseEncoder)
    encoder._status = MemoryStoreStatus("degraded", "missing")
    assert encoder.encode("我喜歡吃蘋果") == {}
