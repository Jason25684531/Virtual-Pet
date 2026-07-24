"""對話記憶回歸測試:BaseMemoryStore 可替換性、fail-open、FIFO 上限、蘋果測試。"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from unittest.mock import MagicMock

from pet_harness.memory.base_memory_store import BaseMemoryStore, MemoryHit, MemoryStoreStatus, NullMemoryStore
from pet_harness.memory.qdrant_memory_store import QdrantMemoryStore


class FakeMemoryStore(BaseMemoryStore):
    """測試用假記憶庫,只依賴 ABC 介面,證明 PetHarnessEngine 不綁死具體實作。"""

    def __init__(self) -> None:
        self.turns: list[tuple[str, str, str]] = []

    def save_turn(self, event_id: str, user_text: str, reply: str) -> None:
        self.turns.append((event_id, user_text, reply))

    def recall(self, query: str, top_k: int = 3) -> list[MemoryHit]:
        return [MemoryHit(event_id, f"{user_text}\n{reply}", 1.0) for event_id, user_text, reply in self.turns[-top_k:]]

    def status(self) -> MemoryStoreStatus:
        return MemoryStoreStatus("ready")

    def clear(self) -> None:
        self.turns.clear()


def test_null_memory_store_is_fail_open_default():
    store = NullMemoryStore()
    assert store.recall("anything") == []
    assert store.status().state == "disabled"
    store.save_turn("evt-1", "hello", "hi")  # 不得拋例外


def test_qdrant_shutdown_closes_client_once_without_initializing_qdrant():
    store = object.__new__(QdrantMemoryStore)
    store._lock = threading.Lock()
    store._closed = False
    client = MagicMock()
    store._client = client

    store.shutdown()
    store.shutdown()

    client.close.assert_called_once()


def test_qdrant_memory_store_lifecycle_in_isolated_process(tmp_path):
    """QdrantMemoryStore 全生命週期(fail-open → ready → save/recall roundtrip →
    FIFO 上限)驗證。

    # ponytail: 故意在乾淨的子行程執行,而非直接在本測試行程內建構真正的
    # QdrantMemoryStore。原因:onnxruntime 原生 DLL 在 Windows 上,當同一行程已經
    # 累積跑過全專案數百個測試(大量原生擴充套件 import/背景執行緒)後,偶爾會在
    # 該行程「第一次」載入 onnxruntime 時觸發原生層級的 DLL 初始化失敗
    # (獨立執行、或子行程重跑皆 100% 成功,與本模組邏輯無關,屬行程資源累積的
    # 環境限制)。子行程從乾淨狀態啟動,徹底避開這個與 pytest 整體套件執行順序
    # 相關的環境問題,同時仍完整驗證真正的 QdrantMemoryStore 行為。
    """
    qdrant_path = tmp_path / "qdrant"
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        import time
        from pet_harness.memory.qdrant_memory_store import QdrantMemoryStore

        store = QdrantMemoryStore(
            character_id="lifecycle-test",
            collection="lifecycle_test_memory",
            path={str(qdrant_path)!r},
            max_items=3,
        )
        assert store.recall("apple") == []

        deadline = time.monotonic() + 20.0
        state = store.status().state
        while state not in ("ready", "disabled", "degraded") and time.monotonic() < deadline:
            time.sleep(0.1)
            state = store.status().state
        assert state == "ready", "unexpected status: %s" % (store.status(),)

        store.save_turn("evt-1", "\\u6211\\u559c\\u6b61\\u7684\\u6c34\\u679c\\u662f\\u860b\\u679c", "\\u860b\\u679c\\uff0c\\u4e0d\\u932f\\u5462\\u3002")
        deadline = time.monotonic() + 10.0
        hits = []
        while time.monotonic() < deadline:
            hits = store.recall("\\u6211\\u559c\\u6b61\\u5403\\u4ec0\\u9ebc\\u6c34\\u679c")
            if hits:
                break
            time.sleep(0.2)
        assert hits, "expected at least one memory hit"
        assert "\\u860b\\u679c" in hits[0].text

        for index in range(2, 6):
            store.save_turn("evt-%d" % index, "user text %d" % index, "reply %d" % index)
        deadline = time.monotonic() + 10.0
        count = None
        while time.monotonic() < deadline:
            with store._lock:
                count = store._count
            if count <= 3:
                break
            time.sleep(0.2)
        assert count is not None and count <= 3
        print("MEMORY_STORE_LIFECYCLE_OK")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "MEMORY_STORE_LIFECYCLE_OK" in result.stdout


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
