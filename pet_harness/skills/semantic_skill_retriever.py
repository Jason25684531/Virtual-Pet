from __future__ import annotations

import hashlib
import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pet_harness.models.skill import Skill


# ONNX/FastEmbed initialization is not safe to run concurrently on Windows.
_INDEX_LOCK = threading.Lock()


@dataclass(frozen=True)
class SemanticCandidate:
    skill_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrieverStatus:
    state: str = "disabled"
    reason: str | None = None


class BaseSemanticSkillRetriever(ABC):
    @abstractmethod
    def index(self, manifest: dict[str, Any]) -> None: ...

    @abstractmethod
    def search(self, query: str, top_k: int) -> list[SemanticCandidate]: ...

    @abstractmethod
    def status(self) -> RetrieverStatus: ...


def semantic_manifest(skills: list[Skill], *, character_id: str | None, model: str, schema_version: str = "1") -> dict[str, Any]:
    rows = [
        {
            "skill_id": skill.name,
            "document": "\n".join(filter(None, [skill.name, skill.description, " ".join(skill.triggers), skill.behavior, skill.required_tool or ""])),
            "enabled": True,
            "required_tool": skill.required_tool,
        }
        for skill in skills
    ]
    identity = {"character_id": character_id or "default", "model": model, "schema_version": schema_version, "skills": rows}
    manifest_hash = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    for row in rows:
        row.update(schema_version=schema_version, manifest_hash=manifest_hash)
    return {**identity, "manifest_hash": manifest_hash, "rows": rows}


class QdrantFastEmbedRetriever(BaseSemanticSkillRetriever):
    """Local-first Qdrant retriever. Loading is deliberately off the UI thread."""

    def __init__(self, *, mode: str = "local", path: str = "runtime_cache/qdrant", url: str = "", model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", collection: str = "skills") -> None:
        self.mode, self.path, self.url, self.model, self.collection = mode, path, url, model, collection
        self._status = RetrieverStatus("disabled")
        self._manifest_hash: str | None = None
        self._pending_manifest_hash: str | None = None
        self._client: Any = None
        self._lock = threading.Lock()
        self._qdrant_client_class: Any = None

    def index(self, manifest: dict[str, Any]) -> None:
        if manifest.get("manifest_hash") in {self._manifest_hash, self._pending_manifest_hash}:
            return
        if self._qdrant_client_class is None:
            try:
                # The caller is the UI/main thread. Only model loading and
                # embedding are deferred; Windows must not import ONNX in a worker.
                import onnxruntime  # noqa: F401
                from qdrant_client import QdrantClient
                self._qdrant_client_class = QdrantClient
            except ImportError:
                self._status = RetrieverStatus("disabled", "qdrant_or_fastembed_not_installed")
                return
        self._status = RetrieverStatus("loading")
        self._pending_manifest_hash = manifest["manifest_hash"]
        threading.Thread(target=self._run_index, args=(manifest,), daemon=True, name="semantic-skill-index").start()

    def _run_index(self, manifest: dict[str, Any]) -> None:
        with _INDEX_LOCK:
            self._index_worker(manifest)

    def _index_worker(self, manifest: dict[str, Any]) -> None:
        try:
            if self._qdrant_client_class is None:
                raise ImportError
            client = self._qdrant_client_class(":memory:") if self.mode == "memory" else self._qdrant_client_class(path=self.path) if self.mode == "local" else self._qdrant_client_class(url=self.url)
            client.set_model(self.model)
            collection = f"{self.collection}_{manifest['character_id']}"
            client.recreate_collection(collection_name=collection, vectors_config=client.get_fastembed_vector_params())
            rows = manifest["rows"]
            if rows:
                client.add(collection_name=collection, documents=[row["document"] for row in rows], metadata=rows)
            with self._lock:
                self._client, self.collection, self._manifest_hash = client, collection, manifest["manifest_hash"]
                self._pending_manifest_hash = None
                self._status = RetrieverStatus("ready")
        except ImportError:
            self._pending_manifest_hash = None
            self._status = RetrieverStatus("disabled", "qdrant_or_fastembed_not_installed")
        except Exception as exc:  # retrieval must never make a tool call fail open
            self._pending_manifest_hash = None
            self._status = RetrieverStatus("degraded", type(exc).__name__)

    def search(self, query: str, top_k: int) -> list[SemanticCandidate]:
        if self._status.state != "ready" or not query:
            return []
        try:
            with self._lock:
                results = self._client.query(collection_name=self.collection, query_text=query, limit=top_k)
            return [SemanticCandidate(str(item.metadata.get("skill_id", "")), float(item.score), dict(item.metadata or {})) for item in results]
        except Exception as exc:
            self._status = RetrieverStatus("degraded", type(exc).__name__)
            return []

    def status(self) -> RetrieverStatus:
        return self._status
